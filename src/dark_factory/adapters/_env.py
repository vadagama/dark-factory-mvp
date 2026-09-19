"""Environment helpers shared by the adapter configs (``*Config.from_env``)."""

import os
from collections.abc import Mapping, Sequence

__all__ = ["missing_env_vars", "resolve_env"]


def resolve_env(env: Mapping[str, str] | None) -> Mapping[str, str]:
    """The given mapping or, by default, the process environment."""
    return os.environ if env is None else env


def missing_env_vars(required: Sequence[str], env: Mapping[str, str] | None) -> tuple[str, ...]:
    """Names of ``required`` that are unset or blank in ``env``, in the given order."""
    source = resolve_env(env)
    return tuple(name for name in required if not (source.get(name) or "").strip())
