"""Errors of the run-record store (T-061, ADR-015 p.4).

Every failure carries a short, value-free diagnostic: a message names the
offending JSON path, evidence id or rule — never the value that triggered it
(ADR-009). A run record embeds user-supplied text (titles, comments, finding
descriptions), so echoing it in a log line would leak exactly what the
sanitization step is there to catch.
"""


class RunRecordStoreError(ValueError):
    """The run record cannot be published into the ``dark-factory-runs`` repository."""


class UnsafeRunRecordError(RunRecordStoreError):
    """The serialized record may leak a secret or personal data (ADR-015 p.4, ADR-009)."""


class RunRecordTooLargeError(RunRecordStoreError):
    """The serialized record exceeds the maximum record size (ADR-015 p.4).

    Heavy evidence — screenshots, large logs, build output, SBOM — belongs in CI
    artifacts behind ``ArtifactStorePort``; Git keeps the compact index only.
    """


class RunRecordImmutabilityError(RunRecordStoreError):
    """A published run record must never be overwritten (ADR-015 p.4).

    A correction is a new record/revision, not a silent rewrite of history.
    """


class EvidenceChainError(RunRecordStoreError):
    """The evidence chain of the record is incomplete or inconsistent (DoD T-061)."""
