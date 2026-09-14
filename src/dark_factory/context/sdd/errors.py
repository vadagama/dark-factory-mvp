"""Errors of the Native SDD Core (ADR-020).

A dedicated hierarchy: adapters in ``context.sdd`` must not import
``dark_factory.ports`` (the port re-exports the errors it needs), so these
derive from ``Exception`` directly.
"""


class SDDError(Exception):
    """Base class of all Native SDD Core errors."""


class ChangeNotFoundError(SDDError):
    """No ChangeSet with the requested id exists under the factory root."""


class BaselineMismatchError(SDDError):
    """The baseline revision on disk differs from the expected one.

    This is how reconciliation detects parallel changes (docs/sdd-native-core.md
    §14): the caller pins the revision its delta was written against.
    """

    def __init__(self, expected_revision: str, actual_revision: str) -> None:
        self.expected_revision = expected_revision
        self.actual_revision = actual_revision
        super().__init__(
            f"baseline revision mismatch: expected {expected_revision!r}, "
            f"actual {actual_revision!r}"
        )


class MissingArtifactError(SDDError):
    """A file the operation needs (delta artifact, baseline document) is absent."""


class FrontmatterError(SDDError):
    """A markdown artifact lacks required YAML frontmatter fields."""
