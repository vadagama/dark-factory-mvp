"""Idempotency key composition (ADR-006 p.3).

operation_key = execution_id + stage_id + input_revision
attempt_id    = operation_key + attempt_number
effect_key    = operation_key + effect_type + effect_target
"""

from dark_factory.changes.enums import Stage


def operation_key(execution_id: str, stage: Stage, input_revision: str) -> str:
    """Logical operation key: identical for every retry of one stage and input revision."""
    return f"{execution_id}:{stage.value}:{input_revision}"


def attempt_id(key: str, attempt_number: int) -> str:
    """Physical attempt key: distinguishes retries without changing operation identity."""
    return f"{key}:{attempt_number}"


def effect_key(key: str, effect_type: str, effect_target: str) -> str:
    """External effect key (branch/commit/MR/deployment/comment) unique in the effect ledger."""
    return f"{key}:{effect_type}:{effect_target}"
