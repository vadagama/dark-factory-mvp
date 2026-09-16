"""Run-record publisher of ``dark-factory-runs`` (T-061, ADR-015 p.4).

Covers the P0-minimum of the record protocol: deterministic partitioning,
idempotent write, immutability of a published record, secret/size screening,
evidence-chain completeness and the derived views (evidence index, usage
summary, decisions table) — plus the adversarial cases around path safety and
slug collisions.
"""

import hashlib
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from pathlib import Path
from typing import Final

import pytest

from dark_factory.changes.enums import EvidenceType, Role, RunStatus, StageStatus
from dark_factory.changes.refs import ArtifactRef
from dark_factory.changes.run_records import RunRecord, from_json, to_json
from dark_factory.changes.usage import Usage
from dark_factory.execution.runs import (
    MAX_RUN_RECORD_BYTES,
    EvidenceChainError,
    PublishOutcome,
    RetentionClass,
    RunEvidenceEntry,
    RunEvidenceIndex,
    RunRecordImmutabilityError,
    RunRecordRef,
    RunRecordStore,
    RunRecordStoreError,
    RunRecordTooLargeError,
    RunUsageSummary,
    UnsafeRunRecordError,
    build_usage_summary,
    check_payload_size,
    describe_unsafe_values,
    find_unsafe_values,
    read_local_evidence,
    render_decisions,
    resolve_ref_path,
    run_dir,
    slug,
)
from dark_factory.orchestration.budget import RoleUsage
from tests.changes_factories import (
    NOW,
    make_change,
    make_decision,
    make_evidence,
    make_finding,
    make_gate_result,
    make_record,
    make_run,
    make_stage_result,
)

SECRETS: Final[tuple[tuple[str, str], ...]] = (
    ("private_key", "-----BEGIN RSA PRIVATE KEY-----"),
    ("github_token", "ghp_" + "a" * 36),
    ("bearer_token", "Authorization: Bearer " + "a" * 24),
    ("jwt", "eyJhbGciOiJIUzI1NiJ9.eyJzdWIiOiIxIn0.abcd1234"),
    ("aws_access_key", "AKIA" + "A" * 16),
    ("secret_assignment", "password=hunter2hunter2"),
    ("uri_userinfo", "https://user:secret@ci.example/report.json"),
)


def record_with_evidence(
    *,
    required: bool = False,
    available: bool = True,
    checksum: str | None = "sha256:abc123",
    uri: str = "https://ci.example/artifacts/1",
) -> RunRecord:
    """Succeeded record whose construction stage declares one referenced evidence."""
    evidence = make_evidence(required=required, available=available).model_copy(
        update={"checksum": checksum, "uri": uri}
    )
    result = make_stage_result(
        status=StageStatus.SUCCEEDED,
        evidence=[evidence],
        gate_results=[make_gate_result()],
    )
    return make_record(RunStatus.SUCCEEDED, [result])


def with_description(record: RunRecord, description: str) -> RunRecord:
    """Same record with a different change description (the screening carrier)."""
    return record.model_copy(
        update={"change": record.change.model_copy(update={"description": description})}
    )


# --- partitioning and path safety (ADR-015 p.4, git-structure §8) --------------


def test_slug_is_deterministic_and_portable() -> None:
    assert slug("chg:product:2026:0001") == "chg-product-2026-0001"
    assert slug("run_01H.test-1") == "run_01H.test-1"


@pytest.mark.parametrize("identifier", ["", " ", "..", ".", "...", "///", "-"])
def test_slug_rejects_identifiers_without_a_safe_path_element(identifier: str) -> None:
    with pytest.raises(RunRecordStoreError):
        slug(identifier)


def test_slug_rejects_a_very_long_identifier() -> None:
    with pytest.raises(RunRecordStoreError):
        slug("a" * 121)


def test_run_dir_partitions_by_year_month_change_and_run(tmp_path: Path) -> None:
    assert run_dir(tmp_path, make_record()) == (
        tmp_path / "runs" / "2026" / "09" / "chg-001" / "run-001"
    )


@pytest.mark.parametrize(
    ("moment", "expected"),
    [
        (datetime(2026, 1, 1, 2, 0, tzinfo=timezone(timedelta(hours=5))), ("2025", "12")),
        (datetime(2026, 12, 31, 23, 30, tzinfo=timezone(timedelta(hours=-5))), ("2027", "01")),
    ],
)
def test_run_dir_partitions_by_utc_and_not_by_local_time(
    tmp_path: Path, moment: datetime, expected: tuple[str, str]
) -> None:
    record = make_record().model_copy(
        update={"run": make_run().model_copy(update={"created_at": moment})}
    )
    assert run_dir(tmp_path, record).parts[-4:-2] == expected


def test_run_dir_rejects_an_unsafe_change_id(tmp_path: Path) -> None:
    record = make_record().model_copy(
        update={"change": make_change().model_copy(update={"id": ".."})}
    )
    with pytest.raises(RunRecordStoreError):
        run_dir(tmp_path, record)


@pytest.mark.parametrize("relative", ["", ".", "..", "runs/../../etc", "/abs/record"])
def test_resolve_ref_path_rejects_unsafe_paths(tmp_path: Path, relative: str) -> None:
    with pytest.raises(RunRecordStoreError):
        resolve_ref_path(tmp_path, relative)


# --- write protocol ------------------------------------------------------------


def test_publish_writes_the_record_tree_of_the_runs_repository(tmp_path: Path) -> None:
    store = RunRecordStore(tmp_path)

    result = store.publish(record_with_evidence())

    assert result.outcome is PublishOutcome.CREATED
    assert result.ref.relative_path == "runs/2026/09/chg-001/run-001"
    directory = tmp_path / result.ref.relative_path
    assert sorted(entry.name for entry in directory.iterdir()) == [
        "evidence-index.json",
        "manifest.yaml",
        "snapshot.json",
        "stages",
        "usage.json",
    ]
    assert [entry.name for entry in (directory / "stages").iterdir()] == ["construction-1.json"]
    snapshot = (directory / "snapshot.json").read_bytes()
    assert result.ref.digest == hashlib.sha256(snapshot).hexdigest()
    assert from_json(RunRecord, snapshot.decode("utf-8")) == record_with_evidence()


def test_publish_twice_is_idempotent_and_does_not_rewrite(tmp_path: Path) -> None:
    store = RunRecordStore(tmp_path)
    record = record_with_evidence()
    first = store.publish(record)
    snapshot = tmp_path / first.ref.relative_path / "snapshot.json"
    written_at = snapshot.stat().st_mtime_ns

    second = store.publish(record)

    assert second.outcome is PublishOutcome.UNCHANGED
    assert second.ref == first.ref
    assert snapshot.stat().st_mtime_ns == written_at


def test_publish_refuses_to_overwrite_a_published_record(tmp_path: Path) -> None:
    store = RunRecordStore(tmp_path)
    store.publish(record_with_evidence())
    changed = record_with_evidence().model_copy(
        update={"change": make_change().model_copy(update={"title": "Another title"})}
    )

    with pytest.raises(RunRecordImmutabilityError):
        store.publish(changed)


def test_publish_rejects_two_results_of_the_same_attempt(tmp_path: Path) -> None:
    result = make_stage_result(status=StageStatus.SUCCEEDED)
    record = make_record(RunStatus.RUNNING, [result, result])

    with pytest.raises(RunRecordStoreError) as excinfo:
        RunRecordStore(tmp_path).publish(record)

    assert "two stage results for the same attempt" in str(excinfo.value)


def test_a_run_without_stage_results_has_no_stages_directory(tmp_path: Path) -> None:
    ref = RunRecordStore(tmp_path).publish(make_record(RunStatus.RUNNING)).ref

    assert not (tmp_path / ref.relative_path / "stages").exists()


def test_decisions_section_is_published_when_present(tmp_path: Path) -> None:
    record = make_record(RunStatus.RUNNING).model_copy(update={"decisions": [make_decision()]})

    ref = RunRecordStore(tmp_path).publish(record).ref

    decisions = (tmp_path / ref.relative_path / "decisions.md").read_text(encoding="utf-8")
    assert decisions.startswith("# Decisions")
    assert "dec-1" in decisions


def test_published_evidence_index_matches_the_built_index(tmp_path: Path) -> None:
    store = RunRecordStore(tmp_path)
    record = record_with_evidence()

    ref = store.publish(record).ref

    persisted = (tmp_path / ref.relative_path / "evidence-index.json").read_text(encoding="utf-8")
    assert from_json(RunEvidenceIndex, persisted) == store.build_evidence_index(record)


# --- screening: secrets and size (ADR-015 p.4, ADR-009) ------------------------


def test_find_unsafe_values_reports_json_paths() -> None:
    payload = {"a": [{"b": "ghp_" + "a" * 36}], "c": "plain"}

    values = find_unsafe_values(payload)

    assert [(value.path, value.kind) for value in values] == [("a[0].b", "github_token")]
    assert describe_unsafe_values(values) == "a[0].b [github_token]"


@pytest.mark.parametrize(("kind", "secret"), SECRETS)
def test_publish_rejects_secrets_without_echoing_them(
    tmp_path: Path, kind: str, secret: str
) -> None:
    record = with_description(record_with_evidence(), f"see {secret} for details")

    with pytest.raises(UnsafeRunRecordError) as excinfo:
        RunRecordStore(tmp_path).publish(record)

    message = str(excinfo.value)
    assert "change.description" in message
    assert kind in message
    assert secret not in message
    assert not (tmp_path / "runs").exists()


def test_publish_rejects_an_oversized_record(tmp_path: Path) -> None:
    record = with_description(record_with_evidence(), "x" * (MAX_RUN_RECORD_BYTES + 1))

    with pytest.raises(RunRecordTooLargeError):
        RunRecordStore(tmp_path).publish(record)

    assert not (tmp_path / "runs").exists()


def test_payload_size_boundary() -> None:
    check_payload_size(MAX_RUN_RECORD_BYTES)
    with pytest.raises(RunRecordTooLargeError):
        check_payload_size(MAX_RUN_RECORD_BYTES + 1)


# --- evidence chain ------------------------------------------------------------


def test_publish_rejects_a_reference_to_an_undeclared_evidence(tmp_path: Path) -> None:
    finding = make_finding().model_copy(update={"evidence_ids": ["ev-missing"]})
    result = make_stage_result(status=StageStatus.SUCCEEDED, findings=[finding])
    record = make_record(RunStatus.SUCCEEDED, [result])

    with pytest.raises(EvidenceChainError) as excinfo:
        RunRecordStore(tmp_path).publish(record)

    assert "unresolved evidence reference: ev-missing" in str(excinfo.value)


def test_publish_rejects_duplicate_evidence_ids_with_conflicting_sources(tmp_path: Path) -> None:
    first = make_stage_result(status=StageStatus.SUCCEEDED, evidence=[make_evidence()])
    second = make_stage_result(
        status=StageStatus.SUCCEEDED,
        evidence=[make_evidence().model_copy(update={"uri": "https://ci.example/artifacts/2"})],
    ).model_copy(update={"attempt_number": 2})
    record = make_record(RunStatus.SUCCEEDED, [first, second])

    with pytest.raises(EvidenceChainError) as excinfo:
        RunRecordStore(tmp_path).publish(record)

    assert "duplicate evidence id with conflicting source: ev-1" in str(excinfo.value)


def test_publish_rejects_a_moving_evidence_uri(tmp_path: Path) -> None:
    record = record_with_evidence(uri="https://ci.example/artifacts/latest/report.json")

    with pytest.raises(EvidenceChainError) as excinfo:
        RunRecordStore(tmp_path).publish(record)

    assert "evidence URI is not immutable: ev-1" in str(excinfo.value)


def test_required_evidence_of_a_succeeded_run_needs_a_checksum(tmp_path: Path) -> None:
    record = record_with_evidence(required=True, checksum=None)

    with pytest.raises(EvidenceChainError) as excinfo:
        RunRecordStore(tmp_path).publish(record)

    assert "required evidence without a checksum: ev-1" in str(excinfo.value)


def test_unavailable_required_evidence_blocks_a_succeeded_run(tmp_path: Path) -> None:
    record = record_with_evidence(required=True, available=False)

    with pytest.raises(EvidenceChainError) as excinfo:
        RunRecordStore(tmp_path).publish(record)

    assert "unavailable" in str(excinfo.value)


def test_a_failed_run_with_incomplete_evidence_is_still_recorded(tmp_path: Path) -> None:
    evidence = make_evidence(required=True, available=False).model_copy(update={"checksum": None})
    result = make_stage_result(status=StageStatus.FAILED, evidence=[evidence])
    record = make_record(RunStatus.FAILED, [result])

    published = RunRecordStore(tmp_path).publish(record)

    assert published.outcome is PublishOutcome.CREATED


# --- evidence index and checksums ----------------------------------------------


def test_evidence_index_maps_evidence_and_artifacts(tmp_path: Path) -> None:
    artifact = ArtifactRef(
        artifact_type="application/json",
        uri="https://ci.example/artifacts/2",
        sha256="sha256:def456",
        producer="ci",
    )
    result = make_stage_result(
        status=StageStatus.SUCCEEDED,
        evidence=[make_evidence(required=True)],
        gate_results=[make_gate_result()],
    ).model_copy(update={"artifacts": [artifact]})
    record = make_record(RunStatus.SUCCEEDED, [result])

    index = RunRecordStore(tmp_path).build_evidence_index(record)

    assert (index.change_id, index.run_id) == ("chg-001", "run-001")
    evidence_entry, artifact_entry = index.entries
    assert evidence_entry.id == "ev-1"
    assert evidence_entry.type == EvidenceType.REPORT.value
    assert evidence_entry.checksum == "sha256:abc123"
    assert evidence_entry.producer == "construction:1"
    assert evidence_entry.created_at == NOW
    assert evidence_entry.retention_class is RetentionClass.AUDIT
    assert artifact_entry.id == "artifact:construction:1:0"
    assert artifact_entry.type == "application/json"
    assert artifact_entry.media_type == "application/json"
    assert artifact_entry.checksum == "sha256:def456"
    assert artifact_entry.producer == "ci"
    assert artifact_entry.retention_class is RetentionClass.STANDARD


def test_verify_evidence_checksums_reports_only_mismatches(tmp_path: Path) -> None:
    store = RunRecordStore(tmp_path)
    payload = b"evidence payload"
    index = RunEvidenceIndex(
        change_id="chg-001",
        run_id="run-001",
        entries=(
            RunEvidenceEntry(
                id="ev-1",
                type="report",
                uri="mem://1",
                checksum=hashlib.sha256(payload).hexdigest(),
            ),
            RunEvidenceEntry(
                id="ev-2", type="report", uri="mem://2", checksum="sha256:" + "0" * 64
            ),
            RunEvidenceEntry(id="ev-3", type="report", uri="mem://3"),
            RunEvidenceEntry(
                id="ev-4", type="report", uri="mem://4", checksum="sha256:" + "1" * 64
            ),
        ),
    )
    resolvable = {"mem://1": payload, "mem://2": payload}

    violations = store.verify_evidence_checksums(index, resolver=resolvable.get)

    assert violations == ("evidence checksum mismatch: ev-2",)


def test_read_local_evidence_reads_file_uris_only(tmp_path: Path) -> None:
    path = tmp_path / "report.json"
    path.write_bytes(b"{}")

    assert read_local_evidence(f"file://{path}") == b"{}"
    assert read_local_evidence("https://ci.example/artifacts/1") is None
    assert read_local_evidence(f"file://{tmp_path}/missing.json") is None


def test_verify_evidence_checksums_resolves_file_uris_by_default(tmp_path: Path) -> None:
    """Without an injected resolver, local files are verified and remote evidence is skipped."""
    path = tmp_path / "report.json"
    path.write_bytes(b"{}")
    index = RunEvidenceIndex(
        change_id="chg-001",
        run_id="run-001",
        entries=(
            RunEvidenceEntry(
                id="ev-1",
                type="report",
                uri=f"file://{path}",
                checksum=hashlib.sha256(b"{}").hexdigest(),
            ),
            RunEvidenceEntry(
                id="ev-2", type="report", uri=f"file://{path}", checksum="sha256:" + "0" * 64
            ),
            RunEvidenceEntry(
                id="ev-3",
                type="report",
                uri="https://ci.example/artifacts/1",
                checksum="sha256:" + "0" * 64,
            ),
        ),
    )

    violations = RunRecordStore(tmp_path).verify_evidence_checksums(index)

    assert violations == ("evidence checksum mismatch: ev-2",)


# --- derived views: usage and decisions ----------------------------------------


def test_usage_summary_aggregates_stage_usage() -> None:
    first = make_stage_result(status=StageStatus.SUCCEEDED).model_copy(
        update={
            "usage": Usage(
                prompt_tokens=10, completion_tokens=5, total_tokens=15, cost=Decimal("0.10")
            )
        }
    )
    second = make_stage_result(status=StageStatus.SUCCEEDED).model_copy(
        update={"attempt_number": 2, "usage": Usage(prompt_tokens=1, completion_tokens=2)}
    )
    record = make_record(RunStatus.RUNNING, [first, second])

    summary = build_usage_summary(record)

    assert [(stage.stage, stage.attempt_number) for stage in summary.stages] == [
        ("construction", 1),
        ("construction", 2),
    ]
    assert summary.totals.prompt_tokens == 11
    assert summary.totals.completion_tokens == 7
    assert summary.totals.total_tokens == 15
    assert summary.totals.cost == Decimal("0.10")
    assert summary.budget == record.run.budget


def test_usage_summary_keeps_unreported_totals_absent() -> None:
    record = make_record(RunStatus.RUNNING, [make_stage_result(status=StageStatus.SUCCEEDED)])

    summary = build_usage_summary(record)

    assert summary.totals.prompt_tokens == 0
    assert summary.totals.total_tokens is None
    assert summary.totals.cost is None


def test_usage_summary_carries_the_per_role_aggregate() -> None:
    """T-062: the per-role budget rides along as an additive, optional section."""
    record = make_record(RunStatus.RUNNING, [make_stage_result(status=StageStatus.SUCCEEDED)])
    roles = (
        RoleUsage(role=Role.DEVELOP, usage=Usage(total_tokens=15, cost=Decimal("0.10")), calls=1),
        RoleUsage(role=Role.QUALITY, reserved=Usage(total_tokens=5)),
    )

    summary = build_usage_summary(record, roles=roles)
    baseline = build_usage_summary(record)

    # Present only when the caller has a coordinator aggregate: the field defaults to empty,
    # so a summary written before T-062 still validates (ADR-015 p.3, additive change).
    assert baseline.roles == ()
    assert summary.roles == roles
    assert [item.role for item in summary.roles] == [Role.DEVELOP, Role.QUALITY]
    assert summary.budget == baseline.budget
    assert summary.stages == baseline.stages
    assert summary.totals == baseline.totals
    assert RunUsageSummary.model_validate_json(summary.model_dump_json()) == summary


def test_render_decisions_renders_an_escaped_table() -> None:
    decision = make_decision().model_copy(update={"comment": "line|one\nline two"})

    lines = render_decisions([decision]).splitlines()

    assert lines[0] == "# Decisions"
    assert lines[2] == (
        "| id | gate | outcome | decided_by | role | decided_at | commit_sha | comment |"
    )
    assert lines[3] == "| --- | --- | --- | --- | --- | --- | --- | --- |"
    assert lines[4].startswith("| dec-1 | planning | approved | human | product |")
    assert lines[4].endswith("| - | line\\|one line two |")


# --- lookup and read-back (DoD: idempotent addressability by change_id) --------


def test_find_existing_and_list_runs_are_deterministic(tmp_path: Path) -> None:
    store = RunRecordStore(tmp_path)
    first = make_record()
    second = make_record().model_copy(
        update={"run": make_run().model_copy(update={"id": "run-002"})}
    )
    store.publish(first)
    store.publish(second)

    refs = store.list_runs("chg-001")

    assert [ref.run_id for ref in refs] == ["run-001", "run-002"]
    latest = store.find_existing("chg-001")
    assert latest is not None
    assert latest.run_id == "run-002"
    assert store.find_existing("chg-001", run_id="run-001") == refs[0]
    assert store.find_existing("chg-999") is None


def test_lookup_is_scoped_by_change_id_despite_slug_collisions(tmp_path: Path) -> None:
    store = RunRecordStore(tmp_path)
    colon = make_record().model_copy(
        update={
            "change": make_change().model_copy(update={"id": "a:b"}),
            "run": make_run().model_copy(update={"id": "run-001", "change_id": "a:b"}),
        }
    )
    dash = make_record().model_copy(
        update={
            "change": make_change().model_copy(update={"id": "a-b"}),
            "run": make_run().model_copy(update={"id": "run-002", "change_id": "a-b"}),
        }
    )
    store.publish(colon)
    store.publish(dash)

    # Both ids map to one directory name; each lookup still returns its own record.
    assert slug("a:b") == slug("a-b") == "a-b"
    found = store.find_existing("a:b")
    assert found is not None
    assert found.change_id == "a:b"
    assert [ref.change_id for ref in store.list_runs("a-b")] == ["a-b"]


def test_read_record_round_trips_the_published_record(tmp_path: Path) -> None:
    store = RunRecordStore(tmp_path)
    record = record_with_evidence()

    ref = store.publish(record).ref

    assert store.read_record(ref) == record


def test_read_record_rejects_an_escaping_ref(tmp_path: Path) -> None:
    ref = RunRecordRef(
        change_id="chg-001", run_id="run-001", relative_path="../outside", digest="d"
    )

    with pytest.raises(RunRecordStoreError):
        RunRecordStore(tmp_path).read_record(ref)


def test_lookup_reports_a_corrupt_record(tmp_path: Path) -> None:
    store = RunRecordStore(tmp_path)
    ref = store.publish(make_record()).ref
    (tmp_path / ref.relative_path / "snapshot.json").write_text("{", encoding="utf-8")

    with pytest.raises(RunRecordStoreError):
        store.find_existing("chg-001")


def test_published_record_is_plain_json_without_binary_payloads(tmp_path: Path) -> None:
    """Git material only: no screenshots, logs or build output in the record tree."""
    store = RunRecordStore(tmp_path)

    ref = store.publish(record_with_evidence()).ref

    directory = tmp_path / ref.relative_path
    for path in directory.rglob("*"):
        if path.is_file():
            assert path.suffix in {".json", ".yaml", ".md"}
    assert to_json(record_with_evidence()) == (directory / "snapshot.json").read_text(
        encoding="utf-8"
    )
