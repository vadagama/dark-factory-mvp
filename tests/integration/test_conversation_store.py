"""Discussion and document repositories against PostgreSQL (T078-T085, ADR-034/ADR-035).

Requires ``DARK_FACTORY_TEST_DATABASE_URL``; skipped without it. The schema comes
from the real Alembic migration (``0006_conversations``), so the CHECK
constraints and the cascade from ``change`` are exercised here.
"""

import os
from pathlib import Path

from alembic import command
from sqlalchemy import Engine, inspect
from sqlalchemy.orm import Session, sessionmaker

from dark_factory.changes import (
    AnswerKind,
    ArtifactAnchor,
    ArtifactDraft,
    ArtifactViewMark,
    Comment,
    CommentStatus,
    Phase,
    Question,
    QuestionStatus,
    ReworkOrder,
    ReworkOrderStatus,
    ReworkSummary,
    Role,
)
from dark_factory.orchestration.state.change_store import ChangeRepository
from dark_factory.orchestration.state.conversation_store import (
    ArtifactDraftRepository,
    ArtifactViewRepository,
    ConversationRepository,
)
from dark_factory.orchestration.state.engine import session_scope
from tests.changes_factories import make_change
from tests.integration.conftest import alembic_config

REPO_ROOT = Path(__file__).resolve().parents[2]
TEST_DATABASE_URL_ENV = "DARK_FACTORY_TEST_DATABASE_URL"
MIGRATION_HEAD = "0006_conversations"
MIGRATION_PARENT = "0005_products"
NEW_TABLES = {"question", "comment", "rework_order", "artifact_draft", "artifact_view"}

ANCHOR = ArtifactAnchor(artifact="spec/requirements/REQ-001.md", anchor_id="REQ-001", revision="r1")


def _seed_change(session_factory: sessionmaker[Session]) -> str:
    change = make_change()
    with session_scope(session_factory) as session:
        ChangeRepository(session).create(change)
    return change.id


def _question(change_id: str, question_id: str = "q_1", **overrides: object) -> Question:
    fields: dict[str, object] = {
        "id": question_id,
        "change_id": change_id,
        "phase": Phase.REQUIREMENTS,
        "text": "Round to cents?",
        "kind": AnswerKind.CHOICE,
        "options": ("yes", "no"),
        "anchor": ANCHOR,
        "asked_by": Role.PRODUCT,
        "run_id": "run_1",
    }
    fields.update(overrides)
    return Question.model_validate(fields)


def test_conversations_migration_is_reversible(state_engine: Engine) -> None:
    config = alembic_config(os.environ[TEST_DATABASE_URL_ENV])
    try:
        command.downgrade(config, MIGRATION_PARENT)
        assert NEW_TABLES.isdisjoint(inspect(state_engine).get_table_names())
    finally:
        command.upgrade(config, MIGRATION_HEAD)
    assert set(inspect(state_engine).get_table_names()) >= NEW_TABLES


def test_questions_round_trip_replay_and_answer(session_factory: sessionmaker[Session]) -> None:
    change_id = _seed_change(session_factory)
    question = _question(change_id)
    with session_scope(session_factory) as session:
        stored, inserted = ConversationRepository(session).add_question(question)
        assert inserted
        replay, inserted_again = ConversationRepository(session).add_question(
            _question(change_id, text="a different text under the same id")
        )
        assert not inserted_again
        assert replay.text == "Round to cents?", "the id is the identity; the replay is inert"
    with session_scope(session_factory) as session:
        repository = ConversationRepository(session)
        loaded = repository.get_question("q_1")
        assert loaded == stored
        loaded.answer_with("yes", answered_by="alice")
        repository.save_question(loaded)
    with session_scope(session_factory) as session:
        repository = ConversationRepository(session)
        answered = repository.get_question("q_1")
        assert answered is not None
        assert answered.status is QuestionStatus.ANSWERED
        assert answered.answer is not None and answered.answer.value == "yes"
        assert repository.list_questions(change_id, status=QuestionStatus.OPEN) == []
        assert [q.id for q in repository.list_questions(change_id, phase=Phase.REQUIREMENTS)] == [
            "q_1"
        ]
        assert repository.list_questions(change_id, artifact=ANCHOR.artifact)[0].id == "q_1"
        assert repository.list_questions(change_id, artifact="other.md") == []


def test_comments_and_rework_orders_round_trip(session_factory: sessionmaker[Session]) -> None:
    change_id = _seed_change(session_factory)
    comment = Comment(
        id="cmt_1",
        change_id=change_id,
        phase=Phase.REQUIREMENTS,
        anchor=ANCHOR,
        body="too vague",
        author="alice",
    )
    order = ReworkOrder(
        id="rw_1",
        change_id=change_id,
        phase=Phase.REQUIREMENTS,
        revisions={ANCHOR.artifact: "r1"},
        comment_ids=("cmt_1",),
        issued_by="alice",
    )
    with session_scope(session_factory) as session:
        repository = ConversationRepository(session)
        assert repository.add_comment(comment)[1]
        assert repository.add_rework_order(order)[1]
        assert not repository.add_rework_order(order)[1]
        comment.rework_order_id = order.id
        repository.save_comment(comment)
    with session_scope(session_factory) as session:
        repository = ConversationRepository(session)
        pending = repository.pending_rework_order(change_id, phase=Phase.REQUIREMENTS)
        assert pending is not None and pending.id == "rw_1"
        assert repository.active_rework_order(change_id) is None
        pending.start(round=1, run_id="run_1")
        repository.save_rework_order(pending)
    with session_scope(session_factory) as session:
        repository = ConversationRepository(session)
        assert repository.pending_rework_order(change_id) is None
        active = repository.active_rework_order(change_id)
        assert active is not None and active.round == 1 and active.run_id == "run_1"
        active.finish(
            ReworkSummary(changed=("REQ-001 reworded",), addressed_comment_ids=("cmt_1",))
        )
        repository.save_rework_order(active)
        stored_comment = repository.get_comment("cmt_1")
        assert stored_comment is not None and stored_comment.rework_order_id == "rw_1"
        stored_comment.mark_addressed("reworded")
        repository.save_comment(stored_comment)
    with session_scope(session_factory) as session:
        repository = ConversationRepository(session)
        done = repository.list_rework_orders(change_id, status=ReworkOrderStatus.DONE)
        assert [o.id for o in done] == ["rw_1"]
        addressed = repository.list_comments(change_id, status=CommentStatus.ADDRESSED)
        assert [c.id for c in addressed] == ["cmt_1"]
        assert repository.list_comments(change_id, artifact="other.md") == []


def test_drafts_upsert_delete_and_view_marks(session_factory: sessionmaker[Session]) -> None:
    change_id = _seed_change(session_factory)
    with session_scope(session_factory) as session:
        drafts = ArtifactDraftRepository(session)
        drafts.put(
            ArtifactDraft(
                change_id=change_id,
                artifact="spec/a.md",
                content="v1",
                base_revision="r1",
                saved_by="alice",
            )
        )
        drafts.put(
            ArtifactDraft(
                change_id=change_id,
                artifact="spec/a.md",
                content="v2",
                base_revision="r1",
                saved_by="alice",
            )
        )
        views = ArtifactViewRepository(session)
        views.mark(
            ArtifactViewMark(
                change_id=change_id, artifact="spec/a.md", revision="r1", viewed_by="alice"
            )
        )
        views.mark(
            ArtifactViewMark(
                change_id=change_id, artifact="spec/a.md", revision="r1", viewed_by="bob"
            )
        )
    with session_scope(session_factory) as session:
        drafts = ArtifactDraftRepository(session)
        draft = drafts.get(change_id, "spec/a.md")
        assert draft is not None and draft.content == "v2", "the newest autosave wins"
        assert [d.artifact for d in drafts.list_for_change(change_id)] == ["spec/a.md"]
        assert drafts.delete(change_id, "spec/a.md")
        assert not drafts.delete(change_id, "spec/a.md")
        views = ArtifactViewRepository(session)
        marks = views.list_for_change(change_id, artifact="spec/a.md")
        assert len(marks) == 1 and marks[0].viewed_by == "alice", "a repeat mark is inert"
        assert views.viewed(change_id, "spec/a.md", "r1")
        assert not views.viewed(change_id, "spec/a.md", "r2")
    with session_scope(session_factory) as session:
        assert ArtifactDraftRepository(session).get(change_id, "spec/a.md") is None
