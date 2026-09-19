"""Document-side records of the factory store: drafts and view marks (T080/T085, ADR-035).

Git is the source of truth of an artifact (ADR-035 p.1); the factory store keeps
only what git must not carry:

* :class:`ArtifactDraft` — the unsaved autosave of one artifact (ADR-035 p.4):
  kept outside git and committed only by an explicit save, so the history never
  fills with autosave noise. ``base_revision`` is the revision the draft was
  started from; a newer head with a different file makes the draft *stale* —
  a read-model fact the API reports, never resolved silently.
* :class:`ArtifactViewMark` — «просмотрено»: the operator opened this revision.
  Deliberately a separate record from a ``Decision`` (ADR-034 p.2, ADR-009
  p.7): viewing a revision is not approving it, and nothing derives an
  approval from a view mark.
"""

from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field

from dark_factory.changes.clock import utc_now

__all__ = ["ArtifactDraft", "ArtifactViewMark"]


class ArtifactDraft(BaseModel):
    """Unsaved edit of one artifact of a change, outside git (ADR-035 p.4)."""

    model_config = ConfigDict(frozen=True)

    change_id: str = Field(min_length=1)
    artifact: str = Field(min_length=1)
    content: str
    base_revision: str | None = None
    """Revision of the artifact the draft was started from (``None`` for a new file)."""
    saved_by: str = Field(min_length=1)
    updated_at: datetime = Field(default_factory=utc_now)


class ArtifactViewMark(BaseModel):
    """The operator viewed this revision of the artifact — not an approval (ADR-034 p.2)."""

    model_config = ConfigDict(frozen=True)

    change_id: str = Field(min_length=1)
    artifact: str = Field(min_length=1)
    revision: str = Field(min_length=1)
    viewed_by: str = Field(min_length=1)
    viewed_at: datetime = Field(default_factory=utc_now)
