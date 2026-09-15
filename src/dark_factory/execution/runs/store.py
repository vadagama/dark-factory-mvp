"""Publisher of run records into the ``dark-factory-runs`` repository (T-061, ADR-015 p.4).

``RunRecordStore`` materializes one ``RunRecord`` as the compact immutable
evidence index of git-structure §8 — ``manifest.yaml`` (the ADR-015 p.5
cross-repository versioning protocol), the self-contained ``snapshot.json``, one
file per immutable ``StageResult``, the ``usage.json`` summary, ``decisions.md``
and ``evidence-index.json`` — and enforces the P0-minimum of the record
protocol:

- **deterministic partitioning** — the same record always lands in the same
  ``runs/<year>/<month>/<change>/<run>`` directory (:mod:`~dark_factory.execution.runs.layout`);
- **idempotent write** — publishing the same record again is a no-op
  (``PublishOutcome.UNCHANGED``), and the lookup by ``change_id`` returns the
  published address (DoD T-061);
- **immutability** — a published record is never overwritten; a different body
  under the same address is rejected, and a correction is a new record;
- **sanitization and size cap** — the record is screened for secrets and kept
  small before it can enter Git;
- **complete evidence chain** — every evidence reference resolves, required
  evidence carries a checksum and evidence URIs are immutable.

Deliberately *not* here (P1, ADR-015 p.4 — recorded in ``docs/tech-dept.md``):
the git commit/push and the single-writer / merge-request-free append protocol,
retention execution and archiving, the manifest signature and the analytics on
top of the index. The store writes the record tree into a runs-repository
checkout; delivering that tree is a CI/CD concern, and the heavy evidence never
passes through it (``ArtifactStorePort``, ADR-009).
"""

import hashlib
import re
import shutil
import tempfile
from collections.abc import Callable, Mapping, Sequence
from decimal import Decimal
from pathlib import Path, PurePosixPath
from typing import Final
from urllib.parse import unquote, urlparse

from pydantic import ValidationError

from dark_factory.changes.enums import RunStatus
from dark_factory.changes.findings import Decision, Finding, GateResult
from dark_factory.changes.refs import ArtifactRef, Evidence
from dark_factory.changes.run_records import RunRecord, from_json, to_json, to_yaml
from dark_factory.changes.usage import Usage
from dark_factory.execution.runs.errors import (
    EvidenceChainError,
    RunRecordImmutabilityError,
    RunRecordStoreError,
    UnsafeRunRecordError,
)
from dark_factory.execution.runs.layout import RUNS_DIR_NAME, resolve_ref_path, run_dir, slug
from dark_factory.execution.runs.models import (
    PublishOutcome,
    PublishResult,
    RetentionClass,
    RunEvidenceEntry,
    RunEvidenceIndex,
    RunRecordRef,
    RunStageUsage,
    RunUsageSummary,
)
from dark_factory.execution.runs.sanitize import (
    check_payload_size,
    describe_unsafe_values,
    find_unsafe_values,
)

MANIFEST_NAME: Final[str] = "manifest.yaml"
"""File with the ``RunManifest`` versioning protocol of the record (ADR-015 p.5)."""

SNAPSHOT_NAME: Final[str] = "snapshot.json"
"""File with the self-contained ``RunRecord`` — the record's primary artifact."""

EVIDENCE_INDEX_NAME: Final[str] = "evidence-index.json"
"""File with the compact evidence index of the run (ADR-015 p.4)."""

USAGE_NAME: Final[str] = "usage.json"
"""File with the token/cost summary of the run."""

DECISIONS_NAME: Final[str] = "decisions.md"
"""File with the human-readable decisions table; omitted when there are none."""

STAGES_DIR_NAME: Final[str] = "stages"
"""Directory with one file per immutable ``StageResult`` of the run."""

_MUTABLE_URI_PARTS: Final[frozenset[str]] = frozenset({"latest"})
"""URI path segments that pin nothing: a record must reference exact revisions."""


class RunRecordStore:
    """Publish and read the run-record index of one runs-repository checkout.

    The store is synchronous and filesystem-only: no network, no git, no clock.
    Both properties matter for the record protocol — an address derived from the
    wall clock would break idempotency, and reaching the network inside the
    publisher would put credentials into the sandboxed stage path (ADR-018).
    """

    def __init__(self, runs_root: Path) -> None:
        self._root = runs_root

    @property
    def runs_root(self) -> Path:
        """The runs-repository checkout this store writes into."""
        return self._root

    def publish(self, record: RunRecord, /) -> PublishResult:
        """Write the record tree idempotently and return its immutable address.

        The checks run before anything is written, so a rejected record leaves no
        partial tree behind. A record that is already published byte-for-byte
        reports ``UNCHANGED``; the same address with different content is an
        immutability violation, never a silent overwrite (ADR-015 p.4).
        """
        directory = run_dir(self._root, record)
        files = self._serialize(record)
        check_payload_size(sum(len(blob) for blob in files.values()))
        unsafe = find_unsafe_values(record.model_dump(mode="json"))
        if unsafe:
            raise UnsafeRunRecordError(
                "the run record is not safe to publish: " + describe_unsafe_values(unsafe)
            )
        violations = self.evidence_chain_violations(record)
        if violations:
            raise EvidenceChainError(
                "the evidence chain of the run record is incomplete: " + "; ".join(violations)
            )
        existing = self._read_snapshot_bytes(directory)
        if existing is not None:
            if existing != files[SNAPSHOT_NAME]:
                raise RunRecordImmutabilityError(self._occupied(directory))
            return PublishResult(
                outcome=PublishOutcome.UNCHANGED, ref=self._ref(directory, files, record)
            )
        written = self._write_atomic(directory, files)
        return PublishResult(
            outcome=PublishOutcome.CREATED if written else PublishOutcome.UNCHANGED,
            ref=self._ref(directory, files, record),
        )

    def find_existing(self, change_id: str, /, *, run_id: str | None = None) -> RunRecordRef | None:
        """Latest published record of ``change_id``, optionally of one run (DoD lookup).

        Deterministic because the candidates are sorted by their address: the
        returned ref is the one with the greatest relative path, which is the
        most recently published record of the change.
        """
        refs = [
            ref
            for ref, record in self._iter_records(change_id)
            if run_id is None or record.run.id == run_id
        ]
        return refs[-1] if refs else None

    def list_runs(self, change_id: str, /) -> tuple[RunRecordRef, ...]:
        """All published records of ``change_id`` in deterministic address order."""
        return tuple(ref for ref, _ in self._iter_records(change_id))

    def read_record(self, ref: RunRecordRef, /) -> RunRecord:
        """Read back the self-contained record of ``ref`` (round-trip of :meth:`publish`)."""
        directory = resolve_ref_path(self._root, ref.relative_path)
        return from_json(RunRecord, (directory / SNAPSHOT_NAME).read_text(encoding="utf-8"))

    def build_evidence_index(self, record: RunRecord, /) -> RunEvidenceIndex:
        """Compact index of every evidence item and artifact of the record.

        Ids are unique by construction: evidence keeps the producer's id, while
        an artifact — which has no id of its own — is addressed by its stage
        attempt and its position in the immutable result.
        """
        entries: list[RunEvidenceEntry] = []
        for result in record.stage_results:
            producer = f"{result.stage.value}:{result.attempt_number}"
            entries.extend(_evidence_entry(evidence, producer) for evidence in result.evidence)
            entries.extend(
                _artifact_entry(artifact, producer, index)
                for index, artifact in enumerate(result.artifacts)
            )
        return RunEvidenceIndex(
            change_id=record.change.id, run_id=record.run.id, entries=tuple(entries)
        )

    def evidence_chain_violations(self, record: RunRecord, /) -> tuple[str, ...]:
        """Value-free list of chain defects; empty means the record may be published.

        Only chain consistency is checked, never the verdict of the run: a
        ``failed`` or ``blocked`` run is legitimately recorded and must stay
        publishable, so the completeness requirement (a required evidence item
        carries a checksum) is as strict as the ADR-009 p.9 completion
        invariants — it applies to a successful terminal status, which is
        exactly the status such a chain has to justify. Referencing an evidence
        id that nothing declares is a defect regardless of the outcome.
        """
        violations = list(record.completion_violations())
        strict = record.run.status is RunStatus.SUCCEEDED
        declared: dict[str, tuple[str, str | None]] = {}
        for result in record.stage_results:
            for evidence in result.evidence:
                source = (evidence.uri, evidence.checksum)
                previous = declared.get(evidence.id)
                if previous is not None and previous != source:
                    violations.append(
                        f"duplicate evidence id with conflicting source: {evidence.id}"
                    )
                declared[evidence.id] = source
                if strict and evidence.required and evidence.checksum is None:
                    violations.append(f"required evidence without a checksum: {evidence.id}")
                if _is_mutable_uri(evidence.uri):
                    violations.append(f"evidence URI is not immutable: {evidence.id}")
            for artifact in result.artifacts:
                if _is_mutable_uri(artifact.uri):
                    violations.append(f"artifact URI is not immutable: {artifact.uri}")
            for reference in _referenced_evidence_ids(result.gate_results, result.findings):
                if reference not in declared:
                    violations.append(f"unresolved evidence reference: {reference}")
        for decision in record.decisions:
            for reference in decision.evidence_ids:
                if reference not in declared:
                    violations.append(f"unresolved evidence reference: {reference}")
        return tuple(violations)

    def verify_evidence_checksums(
        self,
        index: RunEvidenceIndex,
        /,
        *,
        resolver: Callable[[str], bytes | None] | None = None,
    ) -> tuple[str, ...]:
        """Checksum mismatches of the locally resolvable evidence (ADR-015 p.4).

        Unresolvable evidence — remote CI artifacts, object storage — is not a
        violation: the heavy payload lives outside Git by design. The resolver
        seam keeps that I/O out of the core; the default reads ``file://`` URIs.
        """
        read = resolver if resolver is not None else read_local_evidence
        violations: list[str] = []
        for entry in index.entries:
            if entry.checksum is None:
                continue
            payload = read(entry.uri)
            if payload is None:
                continue
            if hashlib.sha256(payload).hexdigest() != _normalized_checksum(entry.checksum):
                violations.append(f"evidence checksum mismatch: {entry.id}")
        return tuple(violations)

    def _serialize(self, record: RunRecord) -> dict[str, bytes]:
        """The record tree of git-structure §8, keyed by relative posix path.

        Optional sections are not materialized as empty files (the repository
        rule for generated artifacts): ``decisions.md`` appears only when there
        are decisions, and no ``stages/`` file exists for a run without results.
        """
        files: dict[str, bytes] = {
            MANIFEST_NAME: to_yaml(record.manifest).encode("utf-8"),
            SNAPSHOT_NAME: to_json(record).encode("utf-8"),
            EVIDENCE_INDEX_NAME: to_json(self.build_evidence_index(record)).encode("utf-8"),
            USAGE_NAME: to_json(build_usage_summary(record)).encode("utf-8"),
        }
        for result in record.stage_results:
            name = f"{STAGES_DIR_NAME}/{result.stage.value}-{result.attempt_number}.json"
            if name in files:
                # Two results of the same attempt would collide on one path, and
                # the tree must describe the record exactly, not the last writer.
                raise RunRecordStoreError(
                    "the run record holds two stage results for the same attempt: "
                    f"{result.stage.value} attempt {result.attempt_number}"
                )
            files[name] = to_json(result).encode("utf-8")
        if record.decisions:
            files[DECISIONS_NAME] = render_decisions(record.decisions).encode("utf-8")
        return files

    def _iter_records(self, change_id: str) -> list[tuple[RunRecordRef, RunRecord]]:
        """Published records of ``change_id``, read back and ordered by address.

        The partition is addressed through the id slug, which is a lossy mapping,
        so every candidate is confirmed against the change id it carries: a
        collision can never let one change read another change's record.
        """
        found: list[tuple[RunRecordRef, RunRecord]] = []
        for path in self._record_paths(change_id):
            try:
                record = from_json(RunRecord, path.read_text(encoding="utf-8"))
            except (OSError, ValidationError) as exc:
                raise RunRecordStoreError(
                    f"cannot read the run record at {self._relative(path.parent).as_posix()}"
                ) from exc
            if record.change.id != change_id:
                continue
            found.append((self._ref_from_path(path.parent, record), record))
        return found

    def _record_paths(self, change_id: str) -> list[Path]:
        """Candidate snapshot paths of ``change_id`` across the year/month partitions."""
        pattern = f"{RUNS_DIR_NAME}/*/*/{slug(change_id)}/*/{SNAPSHOT_NAME}"
        return sorted(self._root.glob(pattern))

    def _ref_from_path(self, directory: Path, record: RunRecord) -> RunRecordRef:
        """Address of a record that was read back from disk."""
        digest = hashlib.sha256((directory / SNAPSHOT_NAME).read_bytes()).hexdigest()
        return RunRecordRef(
            change_id=record.change.id,
            run_id=record.run.id,
            relative_path=self._relative(directory).as_posix(),
            digest=digest,
        )

    def _ref(self, directory: Path, files: Mapping[str, bytes], record: RunRecord) -> RunRecordRef:
        """Address of a record that is being written (digest without a re-read)."""
        return RunRecordRef(
            change_id=record.change.id,
            run_id=record.run.id,
            relative_path=self._relative(directory).as_posix(),
            digest=hashlib.sha256(files[SNAPSHOT_NAME]).hexdigest(),
        )

    def _relative(self, directory: Path) -> Path:
        return directory.relative_to(self._root)

    def _occupied(self, directory: Path) -> str:
        """Value-free immutability diagnostic naming the occupied address."""
        return (
            "a different run record is already published at"
            f" {self._relative(directory).as_posix()};"
            " a correction is a new record, not a rewrite (ADR-015 p.4)"
        )

    def _read_snapshot_bytes(self, directory: Path) -> bytes | None:
        """Raw snapshot of an already published record, or ``None`` when there is none."""
        path = directory / SNAPSHOT_NAME
        if not path.is_file():
            return None
        return path.read_bytes()

    def _write_atomic(self, directory: Path, files: Mapping[str, bytes]) -> bool:
        """Write the whole tree under a staging directory and rename it into place.

        The rename is the commit point of the record: a reader either sees the
        complete tree or nothing. A target that appeared meanwhile means a
        concurrent writer won the race — when it wrote the same record the write
        reports ``False`` (``UNCHANGED``), and a different record raises instead
        of being overwritten (ADR-015 p.4; the single-writer protocol that would
        remove the race is P1, ``docs/tech-dept.md``).
        """
        directory.parent.mkdir(parents=True, exist_ok=True)
        staging = Path(tempfile.mkdtemp(prefix=f".{directory.name}-", dir=directory.parent))
        try:
            for name, blob in files.items():
                target = staging.joinpath(*PurePosixPath(name).parts)
                target.parent.mkdir(parents=True, exist_ok=True)
                target.write_bytes(blob)
            try:
                staging.rename(directory)
            except OSError as exc:
                existing = self._read_snapshot_bytes(directory)
                if existing is None:
                    raise
                if existing != files[SNAPSHOT_NAME]:
                    raise RunRecordImmutabilityError(self._occupied(directory)) from exc
                return False
            return True
        finally:
            shutil.rmtree(staging, ignore_errors=True)


def build_usage_summary(record: RunRecord, /) -> RunUsageSummary:
    """Token/cost summary of a run, aggregated from its immutable stage results.

    A stage without a recorded usage contributes zeros rather than being
    dropped, so the summary always lists exactly the attempts of the record; a
    total that no attempt reported stays ``None`` instead of a misleading ``0``.
    """
    stages: list[RunStageUsage] = []
    prompt_tokens = 0
    completion_tokens = 0
    total_tokens: int | None = None
    cost: Decimal | None = None
    for result in record.stage_results:
        usage = result.usage if result.usage is not None else Usage()
        stages.append(
            RunStageUsage(
                stage=result.stage.value, attempt_number=result.attempt_number, usage=usage
            )
        )
        prompt_tokens += usage.prompt_tokens
        completion_tokens += usage.completion_tokens
        if usage.total_tokens is not None:
            total_tokens = (
                usage.total_tokens if total_tokens is None else total_tokens + usage.total_tokens
            )
        if usage.cost is not None:
            cost = usage.cost if cost is None else cost + usage.cost
    return RunUsageSummary(
        run_id=record.run.id,
        budget=record.run.budget,
        stages=tuple(stages),
        totals=Usage(
            prompt_tokens=prompt_tokens,
            completion_tokens=completion_tokens,
            total_tokens=total_tokens,
            cost=cost,
        ),
    )


_DECISION_COLUMNS: Final[tuple[str, ...]] = (
    "id",
    "gate",
    "outcome",
    "decided_by",
    "role",
    "decided_at",
    "commit_sha",
    "comment",
)


def render_decisions(decisions: Sequence[Decision], /) -> str:
    """Human-readable decisions table of a record (``decisions.md``).

    The table is a rendering, not a contract: the machine-readable decisions
    stay in ``snapshot.json``. Cells are escaped so that a comment cannot break
    the table out of its row.
    """
    lines = [
        "# Decisions",
        "",
        "| " + " | ".join(_DECISION_COLUMNS) + " |",
        "| " + " | ".join("---" for _ in _DECISION_COLUMNS) + " |",
    ]
    for decision in decisions:
        cells = (
            _cell(decision.id),
            decision.gate.value,
            decision.outcome.value,
            decision.decided_by.value,
            _cell(decision.role.value if decision.role is not None else None),
            decision.decided_at.isoformat(),
            _cell(decision.commit_sha),
            _cell(decision.comment),
        )
        lines.append("| " + " | ".join(cells) + " |")
    return "\n".join(lines) + "\n"


def read_local_evidence(uri: str, /) -> bytes | None:
    """Bytes of a ``file://`` evidence URI; ``None`` for remote or unreadable URIs.

    Only ``file://`` is resolved: a bare relative path would silently depend on
    the working directory of the caller, and a remote URI belongs to CI
    artifacts, not to this process.
    """
    parsed = urlparse(uri)
    if parsed.scheme != "file":
        return None
    try:
        return Path(unquote(parsed.path)).read_bytes()
    except OSError:
        return None


def _artifact_entry(artifact: ArtifactRef, producer: str, index: int, /) -> RunEvidenceEntry:
    """Index entry of one artifact of a stage result (no id of its own)."""
    return RunEvidenceEntry(
        id=f"artifact:{producer}:{index}",
        type=artifact.artifact_type,
        uri=artifact.uri,
        checksum=artifact.sha256,
        media_type=artifact.artifact_type,
        producer=artifact.producer or producer,
        retention_class=RetentionClass.STANDARD,
    )


def _cell(value: str | None) -> str:
    """One escaped markdown table cell; absent values render as ``-``."""
    if value is None:
        return "-"
    return value.replace("|", "\\|").replace("\n", " ").replace("\r", " ")


def _evidence_entry(evidence: Evidence, producer: str, /) -> RunEvidenceEntry:
    """Index entry of one evidence item; required evidence is audit-classed (ADR-009 p.9)."""
    return RunEvidenceEntry(
        id=evidence.id,
        type=evidence.type.value,
        uri=evidence.uri,
        checksum=evidence.checksum,
        producer=producer,
        created_at=evidence.produced_at,
        retention_class=RetentionClass.AUDIT if evidence.required else RetentionClass.STANDARD,
        required=evidence.required,
        available=evidence.available,
    )


def _is_mutable_uri(uri: str) -> bool:
    """True when a URI carries a moving reference (``latest``) instead of a revision."""
    return any(part.lower() in _MUTABLE_URI_PARTS for part in re.split(r"[/:@]", uri))


def _normalized_checksum(value: str) -> str:
    """Checksum in comparable form: lowercase hex, without the ``sha256:`` prefix."""
    return value.strip().lower().removeprefix("sha256:")


def _referenced_evidence_ids(
    gate_results: Sequence[GateResult], findings: Sequence[Finding]
) -> list[str]:
    """Evidence ids referenced by the gate results and findings of one stage result."""
    references = [evidence_id for gate in gate_results for evidence_id in gate.evidence_ids]
    references.extend(evidence_id for finding in findings for evidence_id in finding.evidence_ids)
    return references
