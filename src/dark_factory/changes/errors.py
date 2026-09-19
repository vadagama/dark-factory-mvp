"""Domain errors shared by the change entities (T-003, T078).

``InvalidStatusTransition`` lives here so the discussion entities
(``changes.conversations``) and the run entities (``changes.run``) raise the
same error without importing each other; ``changes.run`` re-exports it under
its historical name.
"""

__all__ = ["InvalidStatusTransition"]


class InvalidStatusTransition(ValueError):
    """A status pair outside the transition table was requested."""
