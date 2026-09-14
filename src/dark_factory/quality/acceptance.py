"""Independent Quality acceptance (T-013, FR-006, FR-007).

Quality never inherits the author's context: the acceptance context is rebuilt
from the pinned specification, the diff under review and the collected
evidence (:func:`build_acceptance_context`). Findings of both classes —
blocking and non-blocking — plus the human MR comments as data (FR-007) are
weighed into the review-gate result for one specific SHA
(:func:`evaluate_review_gate`); a blocking finding fails the gate, and the
flow policy stops the merge on a failed gate (T-004, data-model §5).

Pure and deterministic — no harness, LLM or clock; identical inputs yield an
identical result. ``quality`` must not depend on ``orchestration``: the flow
consumes this gate, not the other way round (see ``gates.specification``).
"""

from collections.abc import Sequence
from datetime import UTC, datetime
from hashlib import sha256
from typing import Final

from pydantic import BaseModel, ConfigDict, Field

from dark_factory.changes.enums import (
    FindingOrigin,
    FindingSeverity,
    FindingStatus,
    Gate,
    GateStatus,
)
from dark_factory.changes.findings import Finding, GateResult
from dark_factory.context.bundle import ContextBundle, ContextSource, SourceKind, build_bundle

REVIEW_BLOCKING_SEVERITIES: Final[frozenset[FindingSeverity]] = frozenset({FindingSeverity.BLOCKER})
"""Severities the review gate blocks on by default (T-013)."""


class SpecMaterial(BaseModel):
    """Pinned specification of the change under acceptance (FR-003)."""

    model_config = ConfigDict(frozen=True)

    location: str = Field(min_length=1)
    revision: str = Field(min_length=1)
    content: str


class DiffMaterial(BaseModel):
    """Diff under review, produced at the reviewed SHA (FR-006, FR-009)."""

    model_config = ConfigDict(frozen=True)

    repository: str = Field(min_length=1)
    sha: str = Field(min_length=1)
    content: str


class EvidenceMaterial(BaseModel):
    """One collected evidence artifact entering the acceptance context (T-012).

    ``content_hash`` is the sha256 hex digest computed by the collector; the
    acceptance path verifies nothing twice, it records provenance.
    """

    model_config = ConfigDict(frozen=True)

    evidence_id: str = Field(min_length=1)
    location: str = Field(min_length=1)
    content_hash: str = Field(min_length=1)


def build_acceptance_context(
    *,
    change_id: str,
    run_id: str,
    spec: SpecMaterial,
    diff: DiffMaterial,
    evidence: Sequence[EvidenceMaterial] = (),
    now: datetime | None = None,
) -> ContextBundle:
    """Rebuild the acceptance context from the review's own materials (FR-006).

    The bundle is assembled fresh from the pinned specification, the diff at
    the reviewed SHA and the collected evidence — it is never copied from the
    author's bundle. Provenance is computed here: spec and diff hashes from
    their content, evidence hashes from the collector. Identical inputs yield
    an identical ``bundle_hash`` (DoD T-012); ``retrieved_at`` is bookkeeping
    and never enters the hash. Duplicate evidence ids are a ``ValueError``:
    one snapshot of each artifact.
    """
    reference_now = now if now is not None else datetime.now(UTC)
    seen: set[str] = set()
    for item in evidence:
        if item.evidence_id in seen:
            raise ValueError(f"duplicate evidence id {item.evidence_id!r}")
        seen.add(item.evidence_id)
    sources = [
        ContextSource(
            kind=SourceKind.SPEC,
            location=spec.location,
            revision=spec.revision,
            content_hash=_content_hash(spec.content),
            retrieved_at=reference_now,
        ),
        ContextSource(
            kind=SourceKind.REPO,
            location=diff.repository,
            revision=diff.sha,
            content_hash=_content_hash(diff.content),
            retrieved_at=reference_now,
        ),
        *(
            ContextSource(
                kind=SourceKind.EVIDENCE,
                location=item.location,
                revision=None,
                content_hash=item.content_hash,
                retrieved_at=reference_now,
            )
            for item in evidence
        ),
    ]
    return build_bundle(change_id=change_id, run_id=run_id, sources=sources)


def _content_hash(content: str) -> str:
    return sha256(content.encode("utf-8")).hexdigest()


class MrComment(BaseModel):
    """Human MR comment as data (FR-007).

    ``body`` is the raw discussion text: it is recorded, never interpreted —
    a human blocks a merge through the approval decision flow (ADR-018), not
    through comment text. ``evidence_id`` references the exported comment
    artifact that carries the full text and the author.
    """

    model_config = ConfigDict(frozen=True)

    comment_id: str = Field(min_length=1)
    body: str = Field(min_length=1)
    file: str | None = None
    line: int | None = Field(default=None, ge=1)
    reviewed_sha: str | None = None
    evidence_id: str | None = Field(default=None, min_length=1)


def human_comment_findings(comments: Sequence[MrComment], *, reviewed_sha: str) -> list[Finding]:
    """Normalize human MR comments into findings of the common set (FR-007).

    Every comment becomes one ``Finding`` with ``origin=human`` and severity
    ``info``: human remarks enter the findings set as data and never block
    the review gate — the text is not parsed and ``required_action`` stays
    unset. A comment pinned to an older SHA keeps its own ``reviewed_sha``
    and is stale for a later review pass.
    """
    return [
        Finding(
            id=comment.comment_id,
            origin=FindingOrigin.HUMAN,
            severity=FindingSeverity.INFO,
            file=comment.file,
            line=comment.line,
            reviewed_sha=(
                comment.reviewed_sha if comment.reviewed_sha is not None else reviewed_sha
            ),
            status=FindingStatus.OPEN,
            evidence_ids=[comment.evidence_id] if comment.evidence_id is not None else [],
        )
        for comment in comments
    ]


class FindingClassification(BaseModel):
    """Blocking weight of the findings for one review-gate evaluation (T-013).

    ``blocking`` are open findings at the policy severities for the evaluated
    SHA; ``non_blocking`` are the remaining applicable findings; ``stale``
    were reviewed against another SHA — a new SHA invalidates the previous
    review (spec edge cases) and they do not enter the decision.
    """

    model_config = ConfigDict(frozen=True)

    blocking: tuple[Finding, ...] = ()
    non_blocking: tuple[Finding, ...] = ()
    stale: tuple[Finding, ...] = ()


def classify_findings(
    findings: Sequence[Finding],
    *,
    sha: str,
    blocking_severities: frozenset[FindingSeverity] = REVIEW_BLOCKING_SEVERITIES,
) -> FindingClassification:
    """Split findings into blocking, non-blocking and stale for ``sha``.

    A finding blocks when its severity is in ``blocking_severities`` and its
    status is ``open`` — resolved, waived and obsolete findings never block
    (mirrors the completion invariant, data-model §5). Findings reviewed
    against another SHA are stale; a finding without ``reviewed_sha``
    applies to the current review.
    """
    blocking: list[Finding] = []
    non_blocking: list[Finding] = []
    stale: list[Finding] = []
    for finding in findings:
        if finding.reviewed_sha is not None and finding.reviewed_sha != sha:
            stale.append(finding)
        elif finding.severity in blocking_severities and finding.status is FindingStatus.OPEN:
            blocking.append(finding)
        else:
            non_blocking.append(finding)
    return FindingClassification(
        blocking=tuple(blocking),
        non_blocking=tuple(non_blocking),
        stale=tuple(stale),
    )


def evaluate_review_gate(
    findings: Sequence[Finding],
    *,
    sha: str,
    blocking_severities: frozenset[FindingSeverity] = REVIEW_BLOCKING_SEVERITIES,
) -> GateResult:
    """Decide the review gate over the findings of one SHA (T-013, FR-006).

    Deterministic weighing: any blocking finding fails the gate, and a failed
    review gate stops the merge in the flow policy (T-004). ``sha`` is
    recorded on the result — the decision is valid for this SHA only; a new
    SHA invalidates it and the checks repeat (FR-011, spec edge cases). The
    evidence ids of the applicable findings back the decision.
    """
    classification = classify_findings(findings, sha=sha, blocking_severities=blocking_severities)
    status = GateStatus.FAILED if classification.blocking else GateStatus.PASSED
    evidence_ids = sorted(
        {
            evidence_id
            for finding in (*classification.blocking, *classification.non_blocking)
            for evidence_id in finding.evidence_ids
        }
    )
    return GateResult(
        gate=Gate.REVIEW,
        status=status,
        sha=sha,
        summary=_summary(classification, sha),
        evidence_ids=evidence_ids,
    )


def _summary(classification: FindingClassification, sha: str) -> str:
    """Deterministic one-line summary of the review-gate decision."""
    if classification.blocking:
        ids = ", ".join(finding.id for finding in classification.blocking)
        return (
            f"review gate failed: {len(classification.blocking)} blocking finding(s) "
            f"at {sha}: {ids}"
        )
    if classification.non_blocking:
        summary = (
            f"review gate passed: {len(classification.non_blocking)} non-blocking finding(s) "
            f"at {sha}"
        )
    else:
        summary = f"review gate passed: no findings at {sha}"
    if classification.stale:
        summary += f"; {len(classification.stale)} stale finding(s) excluded"
    return summary
