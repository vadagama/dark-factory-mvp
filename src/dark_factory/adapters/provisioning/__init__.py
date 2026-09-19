"""Repository provisioning adapters (ADR-031 p.2).

``LocalMirror`` serves an operator-prepared local mirror (T067); the provider clone
adapter on a GitHub App installation token lands with T068.
"""

from dark_factory.adapters.provisioning.local_mirror import LocalMirror, LocalMirrorConfig

__all__ = ["LocalMirror", "LocalMirrorConfig"]
