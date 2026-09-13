"""Deterministic stage steps (T010): context, machine checks, aggregation, decision."""

from dark_factory.orchestration.stages.checks import (
    budget_exhaustions,
    change_request_missing,
    pending_gate_results,
)
from dark_factory.orchestration.stages.context import StageContext, build_context
from dark_factory.orchestration.stages.executor import run_deterministic_stage, waiting_reason

__all__ = [
    "StageContext",
    "budget_exhaustions",
    "build_context",
    "change_request_missing",
    "pending_gate_results",
    "run_deterministic_stage",
    "waiting_reason",
]
