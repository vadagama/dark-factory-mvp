"""Token authentication of the factory API (T035, ADR-009 p.7, contract api.md).

Read-only endpoints are open on the local contour; every mutating operation
must present ``Authorization: Bearer <token>``. Tokens are configured through
the ``DARK_FACTORY_API_TOKENS`` environment variable (a JSON array of
``{token, actor, role, scopes}`` records); the store keeps only SHA-256
digests and compares them in constant time, so raw tokens are never stored or
logged. An empty store fails closed: every mutating request is rejected.

Roles are ``operator`` and ``service`` only. Approvals additionally require
the operator role: agents never approve (contract api.md, ADR-009 p.7).
Withdrawing a run is an operator decision too (T064, TD-030): agents execute
the pipeline and must not retract the work that gates them.
"""

import hashlib
import hmac
import json
import os
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from typing import Annotated, Final

from fastapi import Depends, Header, HTTPException

API_TOKENS_ENV_VAR: Final[str] = "DARK_FACTORY_API_TOKENS"

OPERATOR_ROLE: Final[str] = "operator"
SERVICE_ROLE: Final[str] = "service"
_ROLE_VALUES: Final[frozenset[str]] = frozenset({OPERATOR_ROLE, SERVICE_ROLE})

SCOPE_CHANGES_WRITE: Final[str] = "changes:write"
SCOPE_APPROVALS_WRITE: Final[str] = "approvals:write"
SCOPE_CI_WRITE: Final[str] = "ci:write"
SCOPE_RUNS_WRITE: Final[str] = "runs:write"


@dataclass(frozen=True, slots=True)
class ApiToken:
    """An authenticated caller: actor, role and granted scopes."""

    actor: str
    role: str
    scopes: frozenset[str]


def _token_digest(token: str) -> str:
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


def _parse_entry(entry: object, position: int) -> tuple[str, ApiToken]:
    """Validate one configuration record; errors never echo the token value."""
    where = f"{API_TOKENS_ENV_VAR}[{position}]"
    if not isinstance(entry, dict):
        raise ValueError(f"{where} must be a JSON object")
    token = entry.get("token")
    actor = entry.get("actor")
    role = entry.get("role")
    scopes = entry.get("scopes", [])
    if not isinstance(token, str) or not token:
        raise ValueError(f"{where}.token must be a non-empty string")
    if not isinstance(actor, str) or not actor:
        raise ValueError(f"{where}.actor must be a non-empty string")
    if not isinstance(role, str) or role not in _ROLE_VALUES:
        raise ValueError(f"{where}.role must be one of: {', '.join(sorted(_ROLE_VALUES))}")
    if not isinstance(scopes, list) or not all(isinstance(item, str) for item in scopes):
        raise ValueError(f"{where}.scopes must be an array of scope strings")
    return token, ApiToken(actor=actor, role=role, scopes=frozenset(scopes))


class ApiTokenStore:
    """Bearer-token lookup by SHA-256 digest; raw tokens are never kept."""

    def __init__(self, tokens: Sequence[tuple[str, ApiToken]] = ()) -> None:
        """``tokens`` maps raw bearer tokens to their identities."""
        self._by_digest: dict[str, ApiToken] = {
            _token_digest(raw): identity for raw, identity in tokens
        }

    @classmethod
    def from_env(cls, environ: Mapping[str, str] | None = None) -> "ApiTokenStore":
        """Build the store from ``DARK_FACTORY_API_TOKENS``; absent/empty means fail-closed."""
        source = os.environ if environ is None else environ
        raw = source.get(API_TOKENS_ENV_VAR, "").strip()
        if not raw:
            return cls()
        try:
            entries = json.loads(raw)
        except json.JSONDecodeError as error:
            raise ValueError(f"{API_TOKENS_ENV_VAR} is not valid JSON") from error
        if not isinstance(entries, list):
            raise ValueError(f"{API_TOKENS_ENV_VAR} must be a JSON array of token entries")
        return cls([_parse_entry(entry, position) for position, entry in enumerate(entries)])

    def lookup(self, presented: str | None) -> ApiToken | None:
        """Identity of the presented token, or ``None`` for unknown/absent credentials."""
        if not presented:
            return None
        digest = _token_digest(presented)
        for known_digest, identity in self._by_digest.items():
            if hmac.compare_digest(known_digest, digest):
                return identity
        return None


def _bearer_credentials(authorization: Annotated[str | None, Header()] = None) -> str | None:
    """Extract the bearer token from ``Authorization``; ``None`` when absent or malformed."""
    if authorization is None:
        return None
    scheme, _, value = authorization.partition(" ")
    if scheme.lower() != "bearer":
        return None
    return value.strip() or None


def require_write(
    store: ApiTokenStore, scope: str, *, require_operator_role: bool = False
) -> Callable[..., ApiToken]:
    """Build a dependency enforcing ``scope`` (and the operator role) for one endpoint.

    A missing or unknown bearer token is 401 with ``WWW-Authenticate: Bearer``;
    a valid token without the scope is 403; endpoints that must be performed by
    a human operator additionally reject the service role (contract api.md).
    """

    def dependency(credentials: Annotated[str | None, Depends(_bearer_credentials)]) -> ApiToken:
        token = store.lookup(credentials)
        if token is None:
            raise HTTPException(
                status_code=401,
                detail="A valid bearer token is required",
                headers={"WWW-Authenticate": "Bearer"},
            )
        if scope not in token.scopes:
            raise HTTPException(status_code=403, detail=f"Scope {scope!r} is required")
        if require_operator_role and token.role != OPERATOR_ROLE:
            raise HTTPException(status_code=403, detail="The operator role is required")
        return token

    return dependency
