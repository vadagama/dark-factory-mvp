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
    ``LocalMirror`` serves an operator-provided mirror but deliberately does not
    apply baseline packs (p.2/p.7). An operation an adapter cannot perform fails
    loudly and names itself — a silent success would be indistinguishable from a
    completed bootstrap, and would fabricate readiness evidence (p.6).
    """

    def __init__(self, operation: str) -> None:
        super().__init__(f"provisioning operation {operation!r} is not supported by this adapter")
        self.operation = operation


class UnsafeWorkspacePathError(PortError, ValueError):
    """A tool call addressed a path outside (or at the root of) the isolated workspace.

    Raised by the execution port and translated by the role tools into a
    model-facing ``error:`` result (found on the M3 live run: a read of ``.``
    passed the tools' check, was refused by the workspace and aborted the
    whole attempt instead of correcting the model's next call).
    """
