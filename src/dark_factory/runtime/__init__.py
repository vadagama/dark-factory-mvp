"""Composition root of the factory: the one layer that binds core to adapters (ADR-024 p.5).

Everything else in ``dark_factory`` is either core (forbidden to import
``dark_factory.adapters``) or an adapter (forbidden to import core beyond
``dark_factory.ports``). That leaves no place to assemble a working factory —
which is exactly why no adapter was ever wired into the working path. This
package is that place, and the import-boundary test holds an explicit allowlist
for it (``tests/test_import_boundaries.py``).

The layer is deliberately thin: configuration in, adapters out, no domain
decisions. A missing configuration yields an absent adapter, never a fallback
behaviour, so a caller can tell "not configured" from "configured and broken"
(the policy the adapter configs already follow). An unconfigured factory is a
valid :class:`Runtime` — it simply cannot build the parts that need the missing
piece, and says so.
"""

from dark_factory.runtime.composition import Runtime, RuntimeNotConfiguredError, build_runtime

__all__ = ["Runtime", "RuntimeNotConfiguredError", "build_runtime"]
