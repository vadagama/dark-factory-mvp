"""Errors raised through port boundaries (shared port-level contract)."""


class PortError(RuntimeError):
    """Base class of contract errors raised by port implementations."""


class HeadMismatchError(PortError):
    """Merge requested with an ``expected_sha`` that does not match the head (FR-011)."""


class RunNotFoundError(PortError):
    """A workflow run id is unknown to the engine."""


class ProvisioningOperationUnsupportedError(PortError):
    """An adapter cannot perform a repository-provisioning operation (ADR-031 p.2).

    ADR-031 p.2 binds two adapters to one port with different capabilities:
    ``LocalMirror`` serves an operator-provided mirror but does not apply
    baseline packs (T069). An operation an adapter cannot perform fails loudly
    and names itself — a silent success would be indistinguishable from a
    completed bootstrap, and would fabricate readiness evidence (p.6).
    """

    def __init__(self, operation: str) -> None:
        super().__init__(f"provisioning operation {operation!r} is not supported by this adapter")
        self.operation = operation
