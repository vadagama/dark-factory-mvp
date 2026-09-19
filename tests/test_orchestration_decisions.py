"""Derived status of decision cards over a fake repository (T093, ADR-039)."""

import asyncio
from datetime import UTC, datetime
from pathlib import Path

from dark_factory.adapters.fakes import FakeRepository
from dark_factory.changes.conversations import ReworkOrder, ReworkSummary
from dark_factory.changes.enums import (
    DecisionOutcome,
    DecisionSource,
    Gate,
    Phase,
    Provider,
    ReworkOrderStatus,
    RiskClass,
)
from dark_factory.changes.findings import Decision
from dark_factory.changes.refs import RepositoryRef
from dark_factory.changes.run import Change
from dark_factory.orchestration.artifacts import ArtifactService
from dark_factory.orchestration.decisions import REPOSITORY_NOT_BOUND, build_decisions_view
from dark_factory.orchestration.stages.agent import branch_name
from dark_factory.orchestration.ui_spec import build_ui_spec_view, dev_url_of

PACK = Path(__file__).resolve().parents[1] / "packs" / "product-baseline" / "changeset" / "design"
REPO = RepositoryRef(provider=Provider.GITHUB, slug="small/calculator")
CHG = ".factory/changes/2026/CHG-0001-percent"
ADR1 = f"{CHG}/design/decisions/ADR-001-example.md"
ADR2 = f"{CHG}/design/decisions/ADR-002-cache.md"
OVERVIEW = f"{CHG}/design/overview.md"
SCN = f"{CHG}/design/ui/scenarios/SCN-001-example.md"
SCR = f"{CHG}/design/ui/screens/SCR-001-example.md"
REQ = f"{CHG}/spec/requirements/REQ-001.md"
FACTORY = ".factory/product/factory.yaml"

ADR2_TEXT = (
    "---\nid: adr:example-product:0002\ntype: adr\ntitle: Cache\nstatus: proposed\n---\n\n"
    "## Decision\n\nCache it.\n"
)


def _change() -> Change:
    return Change(
        id="chg-calc-1",
        title="Percent",
        source="console",
        product=REPO,
        risk_class=RiskClass.R1,
    )


def _decision(outcome: DecisionOutcome, revision: str | None, index: int) -> Decision:
    return Decision(
        id=f"dec_{index}",
        gate=Gate.SPECIFICATION,
        outcome=outcome,
        decided_by=DecisionSource.HUMAN,
        decided_at=datetime(2026, 9, 1, 12, index, tzinfo=UTC),
        commit_sha=revision,
        phase=Phase.ARCHITECTURE,
    )


class Seeded:
    def __init__(self) -> None:
        self.repository = FakeRepository()
        self.branch = branch_name("chg-calc-1")
        asyncio.run(
            self.repository.ensure_branch(REPO, "main", from_revision="base", idempotency_key="m")
        )
        asyncio.run(
            self.repository.ensure_branch(
                REPO, self.branch, from_revision="base", idempotency_key="b"
            )
        )
        self.keys = 0
        self.head = self.commit(
            {
                REQ: b"# REQ\n",
                OVERVIEW: (PACK / "overview.md").read_bytes(),
                ADR1: (PACK / "decisions" / "ADR-001-example.md").read_bytes(),
                ADR2: ADR2_TEXT.encode(),
                SCN: (PACK / "ui" / "scenarios" / "SCN-001-example.md").read_bytes(),
                SCR: (PACK / "ui" / "screens" / "SCR-001-example.md").read_bytes(),
                FACTORY: b"schema: dark-factory.dev/factory/v1\ndev_url: https://dev.calc.test\n",
            }
        )
        self.artifacts = ArtifactService(self.repository)

    def commit(self, files: dict[str, bytes]) -> str:
        self.keys += 1
        self.head = asyncio.run(
            self.repository.publish_commit(
                REPO, self.branch, files, message=f"c{self.keys}", idempotency_key=f"k{self.keys}"
            )
        )
        return self.head


def _order(
    status: ReworkOrderStatus, *, decision_ids: tuple[str, ...], revisions: dict[str, str]
) -> ReworkOrder:
    order = ReworkOrder(
        id=f"rw_{status.value}",
        change_id="chg-calc-1",
        phase=Phase.ARCHITECTURE,
        revisions=revisions,
        instruction="another option",
        decision_ids=decision_ids,
        issued_by="alice",
    )
    if status is not ReworkOrderStatus.PENDING:
        order.start(round=1, run_id="run_1")
    if status is ReworkOrderStatus.DONE:
        order.finish(ReworkSummary(changed=("ADR-001",)))
    return order


def test_without_a_repository_the_view_is_empty_and_says_why() -> None:
    view = build_decisions_view(_change(), artifacts=None, decisions=(), rework_orders=())
    assert view.decisions == () and view.revision is None and view.approved is False
    assert view.errors == (REPOSITORY_NOT_BOUND,)
    ui = build_ui_spec_view(_change(), artifacts=None)
    assert ui.errors == (REPOSITORY_NOT_BOUND,) and ui.screens == ()


def test_cards_are_proposed_before_any_decision_and_carry_their_own_revision() -> None:
    seeded = Seeded()
    view = build_decisions_view(
        _change(), artifacts=seeded.artifacts, decisions=(), rework_orders=()
    )
    assert view.revision == seeded.head and view.approved is False and view.errors == ()
    assert [c.id for c in view.decisions] == [
        "adr:example-product:0001",
        "adr:example-product:0002",
    ]
    assert {c.status for c in view.decisions} == {"proposed"}
    assert view.decisions[0].revision == seeded.head
    assert view.decisions[0].pending_alternative is None
    assert view.decisions[0].affected_artifacts == ()


def test_current_approval_makes_cards_accepted_and_an_open_order_needs_revision() -> None:
    seeded = Seeded()
    approved = [_decision(DecisionOutcome.APPROVED, seeded.head, 1)]
    view = build_decisions_view(
        _change(), artifacts=seeded.artifacts, decisions=approved, rework_orders=()
    )
    assert view.approved is True and {c.status for c in view.decisions} == {"accepted"}

    pending = _order(
        ReworkOrderStatus.PENDING,
        decision_ids=("adr:example-product:0001",),
        revisions={ADR1: seeded.head},
    )
    view = build_decisions_view(
        _change(), artifacts=seeded.artifacts, decisions=approved, rework_orders=[pending]
    )
    by_id = {c.id: c for c in view.decisions}
    assert by_id["adr:example-product:0001"].status == "needs_revision"
    assert by_id["adr:example-product:0001"].pending_alternative == pending
    assert by_id["adr:example-product:0002"].status == "accepted", "the order is about one ADR"

    running = _order(
        ReworkOrderStatus.IN_PROGRESS,
        decision_ids=("adr:example-product:0002",),
        revisions={},
    )
    view = build_decisions_view(
        _change(), artifacts=seeded.artifacts, decisions=approved, rework_orders=[running]
    )
    assert {c.id: c.status for c in view.decisions} == {
        "adr:example-product:0001": "accepted",
        "adr:example-product:0002": "needs_revision",
    }


def test_an_edit_after_the_approval_marks_only_the_changed_adr_needs_revision() -> None:
    seeded = Seeded()
    approved_at = seeded.head
    decisions = [_decision(DecisionOutcome.APPROVED, approved_at, 1)]
    seeded.commit({ADR2: ADR2_TEXT.replace("Cache it.", "Cache it on disk.").encode()})
    view = build_decisions_view(
        _change(), artifacts=seeded.artifacts, decisions=decisions, rework_orders=()
    )
    assert view.approved is False and view.revision == seeded.head
    by_id = {c.id: c for c in view.decisions}
    assert by_id["adr:example-product:0002"].status == "needs_revision"
    assert by_id["adr:example-product:0002"].revision == seeded.head
    assert by_id["adr:example-product:0001"].status == "proposed", (
        "unchanged, but no longer approved"
    )
    assert by_id["adr:example-product:0001"].revision == approved_at

    # A fresh approval on the new revision settles the phase again.
    decisions.append(_decision(DecisionOutcome.APPROVED, seeded.head, 2))
    view = build_decisions_view(
        _change(), artifacts=seeded.artifacts, decisions=decisions, rework_orders=()
    )
    assert view.approved is True and {c.status for c in view.decisions} == {"accepted"}


def test_superseded_and_waived_and_rejected_only() -> None:
    seeded = Seeded()
    seeded.commit({ADR2: ADR2_TEXT.replace("status: proposed", "status: superseded").encode()})
    approved = [_decision(DecisionOutcome.APPROVED, seeded.head, 1)]
    view = build_decisions_view(
        _change(), artifacts=seeded.artifacts, decisions=approved, rework_orders=()
    )
    assert {c.id: c.status for c in view.decisions} == {
        "adr:example-product:0001": "accepted",
        "adr:example-product:0002": "superseded",
    }
    waived = [_decision(DecisionOutcome.WAIVED, None, 1)]
    view = build_decisions_view(
        _change(), artifacts=seeded.artifacts, decisions=waived, rework_orders=()
    )
    assert view.approved is False
    assert {c.status for c in view.decisions} == {"proposed", "superseded"}
    rejected = [_decision(DecisionOutcome.REJECTED, seeded.head, 1)]
    view = build_decisions_view(
        _change(), artifacts=seeded.artifacts, decisions=rejected, rework_orders=()
    )
    assert view.decisions[0].status == "proposed"


def test_affected_artifacts_are_the_design_files_changed_since_the_done_order() -> None:
    seeded = Seeded()
    issued_at = seeded.head
    done = _order(
        ReworkOrderStatus.DONE,
        decision_ids=("adr:example-product:0001",),
        revisions={
            ADR1: issued_at,
            ADR2: issued_at,
            OVERVIEW: issued_at,
            SCN: issued_at,
            SCR: issued_at,
            REQ: issued_at,
        },
    )
    # The agent's round touched the ADR and the screen; the requirement and ADR-002 are untouched.
    seeded.commit(
        {
            ADR1: (PACK / "decisions" / "ADR-001-example.md").read_bytes() + b"\nRevised.\n",
            SCR: b"---\nid: SCR-001\ntype: ui_screen\ntitle: New\n---\n",
            f"{CHG}/design/ui/screens/SCR-002-new.md": b"---\nid: SCR-002\ntype: ui_screen\n---\n",
        }
    )
    view = build_decisions_view(
        _change(), artifacts=seeded.artifacts, decisions=(), rework_orders=[done]
    )
    by_id = {c.id: c for c in view.decisions}
    assert by_id["adr:example-product:0001"].status == "proposed", "a done order is not open"
    assert by_id["adr:example-product:0001"].affected_artifacts == (
        ADR1,
        SCR,
        f"{CHG}/design/ui/screens/SCR-002-new.md",
    )
    assert by_id["adr:example-product:0002"].affected_artifacts == (), "no order about it"


def test_ui_spec_view_reads_ui_nodes_and_the_product_dev_url() -> None:
    seeded = Seeded()
    view = build_ui_spec_view(_change(), artifacts=seeded.artifacts)
    assert view.change_id == "chg-calc-1" and view.revision == seeded.head
    assert view.dev_url == "https://dev.calc.test"
    assert [s.id for s in view.scenarios] == ["SCN-001"]
    assert [s.id for s in view.screens] == ["SCR-001"]
    assert view.screens[0].preview_url == "https://dev.calc.test/checkout/confirm"
    assert [link.id for link in view.links] == ["SCR-001->SCR-001"]
    assert view.errors == ()
    assert dev_url_of(None) is None and dev_url_of("- a list\n") is None
    assert dev_url_of("dev_url: ''\n") is None and dev_url_of(":: not yaml") is None


def test_no_branch_yet_is_an_empty_view_without_errors() -> None:
    repository = FakeRepository()
    asyncio.run(repository.ensure_branch(REPO, "main", from_revision="base", idempotency_key="m"))
    view = build_decisions_view(
        _change(), artifacts=ArtifactService(repository), decisions=(), rework_orders=()
    )
    assert view.decisions == () and view.errors == () and view.revision is None
    ui = build_ui_spec_view(_change(), artifacts=ArtifactService(repository))
    assert ui.screens == () and ui.errors == () and ui.revision is None
