"""Repository provisioning adapters (ADR-031 p.2).

``LocalMirror`` serves an operator-prepared local mirror (T067); ``ProviderClone``
(``adapters/scm/github``) clones from the provider on a GitHub App installation
token (T068). ``packs.py`` resolves baseline packs into the files a bootstrap
writes (T069), shared by the adapters that apply them.
"""

from dark_factory.adapters.provisioning.local_mirror import LocalMirror, LocalMirrorConfig
from dark_factory.adapters.provisioning.packs import ResolvedPack, load_pack

__all__ = ["LocalMirror", "LocalMirrorConfig", "ResolvedPack", "load_pack"]
