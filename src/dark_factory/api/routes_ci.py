"""CI stage toggle endpoints (T059, ADR-026/ADR-027).

The console switches the factory's own CI stages through these endpoints: the
catalog (``dark_factory.ci.stages``) decides what a stage is, and the toggle port
writes the repository variable the workflow reads as ``vars.CI_SKIP_<JOB>``
(ADR-026).

Failure semantics (fail-closed):

- no toggle port on this contour → the catalog is served with
  ``available=false`` and a reason, and every write is 503;
- a write without the ``ci:write`` scope, or without the operator role, is
  401/403: agents never reconfigure the pipeline that gates them (ADR-011);
- a stage outside the catalog is 404 — a request can never name an arbitrary
  repository variable;
- a provider failure is 502 carrying the adapter's sanitized message, never a
  provider body (ADR-009).

Reads follow the local contour (ADR-009 p.7): GET carries no token.
"""

from collections.abc import Mapping
from typing import Annotated, Final

from fastapi import APIRouter, Depends, HTTPException

from dark_factory.api.auth import SCOPE_CI_WRITE, ApiToken, ApiTokenStore, require_write
from dark_factory.api.dto import CiStagesView, CiStageToggleRequest, CiStageView
from dark_factory.ci.stages import CI_STAGES, SKIP_VALUE, CiStage, is_skipped_value, stage_by_job
from dark_factory.ports import CiStageTogglePort, PortError

UNAVAILABLE_REASON: Final[str] = (
    "CI stage toggles are not configured on this contour: the API needs the"
    " DARK_FACTORY_GITHUB_* credentials and DARK_FACTORY_GITHUB_REPOSITORY_SLUG (ADR-027)"
)
"""Why the catalog is served without state; the console renders it verbatim."""


def _provider_failure(exc: PortError) -> HTTPException:
    """502 for a provider failure, carrying the adapter's sanitized text (ADR-009)."""
    return HTTPException(status_code=502, detail=f"CI stage provider failed: {exc}")


def create_ci_router(
    token_store: ApiTokenStore,
    toggles: CiStageTogglePort | None,
    repository: str | None,
) -> APIRouter:
    """Build the ``/ci`` router; ``toggles=None`` serves the catalog as unavailable."""
    router = APIRouter()
    require_ci_write = require_write(token_store, SCOPE_CI_WRITE, require_operator_role=True)

    async def _read(port: CiStageTogglePort) -> Mapping[str, str]:
        try:
            return await port.values()
        except PortError as exc:
            raise _provider_failure(exc) from None

    def _view(stage: CiStage, values: Mapping[str, str] | None) -> CiStageView:
        if values is None:
            return CiStageView.of(stage, None)
        return CiStageView.of(stage, not is_skipped_value(values.get(stage.variable)))

    @router.get("/ci/stages", response_model=CiStagesView)
    async def list_ci_stages() -> CiStagesView:
        """The catalog with the current state of every toggle; open on the local contour."""
        port = toggles
        if port is None:
            return CiStagesView(
                available=False,
                reason=UNAVAILABLE_REASON,
                stages=[_view(stage, None) for stage in CI_STAGES],
            )
        values = await _read(port)
        return CiStagesView(
            repository=repository,
            available=True,
            stages=[_view(stage, values) for stage in CI_STAGES],
        )

    @router.put("/ci/stages/{job}", response_model=CiStageView)
    async def set_ci_stage(
        job: str,
        body: CiStageToggleRequest,
        token: Annotated[ApiToken, Depends(require_ci_write)],
    ) -> CiStageView:
        """Switch one stage on or off — an idempotent target state, not an event (ADR-026)."""
        stage = stage_by_job(job)
        if stage is None:
            raise HTTPException(status_code=404, detail=f"CI stage {job!r} does not exist")
        port = toggles
        if port is None:
            raise HTTPException(status_code=503, detail=UNAVAILABLE_REASON)
        try:
            # Enabled means "absent": the workflow skips a stage only for the exact
            # skip value (ADR-026), so removing the variable is the enabled state.
            await port.set_value(stage.variable, None if body.enabled else SKIP_VALUE)
        except PortError as exc:
            raise _provider_failure(exc) from None
        return CiStageView.of(stage, body.enabled)

    return router
