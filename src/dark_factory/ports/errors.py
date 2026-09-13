"""Errors raised through port boundaries (shared port-level contract)."""


class PortError(RuntimeError):
    """Base class of contract errors raised by port implementations."""


class HeadMismatchError(PortError):
    """Merge requested with an ``expected_sha`` that does not match the head (FR-011)."""


class RunNotFoundError(PortError):
    """A workflow run id is unknown to the engine."""
