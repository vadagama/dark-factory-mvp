"""Context assembly: project knowledge and materials provided to agents (T-012)."""

from dark_factory.context.bundle import (
    CONTEXT_SCHEMA_VERSION,
    ContextBundle,
    ContextSchemaVersion,
    ContextSource,
    SourceKind,
    build_bundle,
)

__all__ = [
    "CONTEXT_SCHEMA_VERSION",
    "ContextBundle",
    "ContextSchemaVersion",
    "ContextSource",
    "SourceKind",
    "build_bundle",
]
