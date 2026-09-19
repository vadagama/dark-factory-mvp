"""Budget coordinator: limits, allowances, reservations and the run aggregate (T-062).

The coordinator is the single ledger of what a run has spent and what it has
committed to spend. It answers three questions deterministically:

- ``check`` — is the run (and the role) still within its limits?
- ``reserve`` — may this agent call start, and how much may it burn?
- ``settle`` — what did the reserved call actually cost?

Limits and thresholds are not restated here: a check folds the ledger into a
``BudgetSnapshot`` and evaluates it with ``rules.limits``
(``continuation_violations``), so a limit cannot mean two different things
(FR-016/FR-018). Reserved-but-unsettled spend counts as spent, so the shared
attempt budget reserves and accounts the calls of **all** agents, reviews and
rework rounds; unknown spend (``Usage.total_tokens is None``) is conservative —
it cannot be reserved while a limit is configured, and it keeps an open
reservation open until reconciled (FR-018).

Determinism: the reference time is always an explicit parameter (as in
``orchestration/rules/limits.py``), never the wall clock; reservation ids derive from the
caller-supplied key, never from a random source. The coordinator is stateful —
it owns one ledger instance — but holds no global mutable state.
"""

from dataclasses import dataclass, field, replace
from datetime import datetime
from decimal import Decimal
from enum import StrEnum

from pydantic import BaseModel, ConfigDict, Field

from dark_factory.changes.enums import Role
from dark_factory.changes.usage import BudgetSnapshot, RoleUsage, Usage
from dark_factory.orchestration.budget.policy import (
    DEFAULT_BUDGET_POLICY,
    BudgetLimits,
    BudgetPolicy,
)
from dark_factory.orchestration.rules.limits import LimitRule, continuation_violations


class BudgetScope(StrEnum):
    """Scope a limit was evaluated in."""

    RUN = "run"
    ROLE = "role"


class BudgetState(StrEnum):
    """Verdict of a budget check."""

    WITHIN_LIMITS = "within_limits"
    AWAITING_DECISION = "awaiting_decision"


@dataclass(frozen=True)
class BudgetViolation:
    """One triggered limit with its scope; ``reason`` carries the exact numbers."""

    scope: BudgetScope
    role: Role | None
    rule: LimitRule
    reason: str


@dataclass(frozen=True)
class BudgetCheck:
    """Verdict of one check: the state plus every triggered limit in stable order."""

    state: BudgetState
    violations: tuple[BudgetViolation, ...]

    @property
    def diagnostics(self) -> str:
        """Joint diagnostics of all violations, run scope first (stable order)."""
        return "; ".join(violation.reason for violation in self.violations)


@dataclass(frozen=True)
class Reservation:
    """One committed agent call, counted until it is settled.

    ``reservation_id`` is derived from the caller's key and the role: replaying
    the same logical operation addresses the same reservation (ADR-006 p.3).
    """

    reservation_id: str
    role: Role
    estimated: Usage
    opened_at: datetime
    settled: bool


def _zero_usage() -> Usage:
    """No spend, known exactly: zero tokens with the cost still unknown."""
    return Usage(total_tokens=0)


class BudgetAggregate(BaseModel):
    """Combined budget of a run: totals, per-role usage, effective limits, state.

    This is the "совокупный бюджет" (T-062) that travels into the run record;
    ``roles`` is ordered by ``Role.value`` so the same ledger always serializes
    the same way. Totals are what the ledger accounted: tokens are exact (zero
    when nothing was accounted, because ``settle`` never records an unknown
    total), cost stays ``None`` until a call reports one — the usage-summary
    rule, so an unreported cost is not presented as zero spend.
    """

    model_config = ConfigDict(frozen=True)

    run: Usage = Field(default_factory=_zero_usage)
    roles: tuple[RoleUsage, ...] = ()
    limits: BudgetLimits = Field(default_factory=BudgetLimits)
    state: BudgetState = BudgetState.WITHIN_LIMITS


class BudgetExhaustedError(RuntimeError):
    """A reservation was refused: the budget is exhausted or the call is unaccountable.

    Carries the ``BudgetCheck`` that produced the refusal (``check.diagnostics``
    is the machine-readable reason) plus an explicit ``reason`` for the two
    refusals that are not a limit violation.
    """

    def __init__(self, check: BudgetCheck, reason: str | None = None) -> None:
        self.check = check
        self.reason = check.diagnostics if reason is None else reason
        super().__init__(self.reason)


class UnknownReservationError(LookupError):
    """Settlement of a reservation id the coordinator never issued."""


@dataclass
class _RoleLedger:
    """Mutable accumulator of one role; owned by exactly one coordinator."""

    usage: Usage = field(default_factory=_zero_usage)
    calls: int = 0


class BudgetCoordinator:
    """Ledger of the run budget: limits, allowances, reservations and totals (T-062)."""

    def __init__(self, policy: BudgetPolicy = DEFAULT_BUDGET_POLICY) -> None:
        self._policy = policy
        self._ledgers: dict[Role, _RoleLedger] = {}
        self._reservations: dict[str, Reservation] = {}

    @property
    def policy(self) -> BudgetPolicy:
        """The policy this coordinator enforces; the default one sets no limit."""
        return self._policy

    def check(self, role: Role, *, now: datetime) -> BudgetCheck:
        """Evaluate the run limits and the effective limits of ``role`` (FR-016/FR-018).

        The run scope is evaluated against the aggregated usage of all roles and
        comes first, then the role scope against the role's own usage; within a
        scope the rule order is the documented ``continuation_violations`` order
        (token, cost, deadline). Reserved-but-unsettled spend counts as spent.
        """
        violations = [*self._run_scope(now=now), *self._role_scope(role, now=now)]
        state = BudgetState.AWAITING_DECISION if violations else BudgetState.WITHIN_LIMITS
        return BudgetCheck(state=state, violations=tuple(violations))

    def reserve(self, role: Role, *, key: str, estimated: Usage, now: datetime) -> Reservation:
        """Commit one agent call of ``role`` before it runs.

        Replaying the same ``key`` returns the reservation already issued and
        never counts the call twice (idempotent replay, ADR-006 p.3) — including
        an already settled one, whose ``settled`` flag tells the caller. A new
        reservation is refused with :class:`BudgetExhaustedError` when the check
        is ``awaiting_decision``, when the role still has an unsettled
        reservation, or when the estimate is unknown while a limit is configured
        (FR-018: unknown spend cannot be committed).
        """
        reservation_id = _reservation_id(role, key)
        known = self._reservations.get(reservation_id)
        if known is not None:
            return known
        check = self.check(role, now=now)
        if check.state is BudgetState.AWAITING_DECISION:
            raise BudgetExhaustedError(check)
        unsettled = self._unsettled_reservation(role)
        if unsettled is not None:
            raise BudgetExhaustedError(
                check,
                reason=(
                    f"role {role.value} already has an unsettled reservation "
                    f"{unsettled.reservation_id!r}"
                ),
            )
        if estimated.total_tokens is None and self._policy.limits_for(role).configured:
            raise BudgetExhaustedError(
                check,
                reason=(
                    f"role {role.value}: unknown spend cannot be reserved while a limit "
                    "is configured (FR-018)"
                ),
            )
        reservation = Reservation(
            reservation_id=reservation_id,
            role=role,
            estimated=estimated,
            opened_at=now,
            settled=False,
        )
        self._reservations[reservation_id] = reservation
        return reservation

    def settle(self, reservation_id: str, *, usage: Usage | None) -> Reservation:
        """Account the actual usage of a reserved call.

        ``None`` or an unknown usage keeps the reservation open: the estimate
        still counts as spent, so the run stays conservative until the spend is
        reconciled (FR-018). Settling an already settled reservation is
        idempotent (the usage is never counted twice); an unknown id raises.
        """
        reservation = self._reservations.get(reservation_id)
        if reservation is None:
            raise UnknownReservationError(f"unknown reservation {reservation_id!r}")
        if reservation.settled or usage is None or usage.total_tokens is None:
            return reservation
        ledger = self._ledgers.setdefault(reservation.role, _RoleLedger())
        ledger.usage = _add_usage(ledger.usage, usage)
        ledger.calls += 1
        settled = replace(reservation, settled=True)
        self._reservations[reservation_id] = settled
        return settled

    def usage_for(self, role: Role) -> Usage:
        """Settled usage of one role (FR-016: restarts never reset the spend)."""
        return self._settled_usage(role)

    def total_usage(self) -> Usage:
        """Settled usage of the whole run, all roles summed."""
        return self._settled_usage(None)

    def aggregate(self, *, now: datetime) -> BudgetAggregate:
        """Combined budget of the run: totals, per-role usage, limits and state."""
        return BudgetAggregate(
            run=self._settled_usage(None),
            roles=tuple(
                RoleUsage(
                    role=role,
                    usage=self._settled_usage(role),
                    calls=self._calls_for(role),
                    reserved=self._reserved_usage(role),
                )
                for role in self._known_roles()
            ),
            limits=self._policy.run_limits,
            state=self._aggregate_state(now=now),
        )

    def _run_scope(self, *, now: datetime) -> list[BudgetViolation]:
        """Run limits against the aggregated usage of all roles."""
        return self._scope_violations(
            scope=BudgetScope.RUN,
            role=None,
            limits=self._policy.run_limits,
            usage=self._effective_usage(None),
            now=now,
        )

    def _role_scope(self, role: Role, *, now: datetime) -> list[BudgetViolation]:
        """Effective role limits (the tighter of run and allowance) against the role."""
        return self._scope_violations(
            scope=BudgetScope.ROLE,
            role=role,
            limits=self._policy.limits_for(role),
            usage=self._effective_usage(role),
            now=now,
        )

    def _scope_violations(
        self,
        *,
        scope: BudgetScope,
        role: Role | None,
        limits: BudgetLimits,
        usage: Usage,
        now: datetime,
    ) -> list[BudgetViolation]:
        """Triggered limits of one scope, in the documented rule order.

        The comparison is delegated to ``rules.limits``: the ledger is folded
        into a ``BudgetSnapshot`` and evaluated with ``continuation_violations``,
        so a threshold cannot drift between the Flow and the coordinator.
        """
        snapshot = BudgetSnapshot(
            token_budget=limits.token_budget,
            tokens_used=_tokens(usage),
            cost_budget=limits.cost_budget,
            cost_used=_cost(usage),
            deadline=limits.deadline,
        )
        prefix = "" if role is None else f"role {role.value}: "
        return [
            BudgetViolation(
                scope=scope,
                role=role,
                rule=violation.rule,
                reason=f"{prefix}{violation.reason}",
            )
            for violation in continuation_violations(snapshot, now=now)
        ]

    def _aggregate_state(self, *, now: datetime) -> BudgetState:
        """Awaiting decision when any scope of any known role is exhausted."""
        if self._run_scope(now=now):
            return BudgetState.AWAITING_DECISION
        if any(self._role_scope(role, now=now) for role in self._known_roles()):
            return BudgetState.AWAITING_DECISION
        return BudgetState.WITHIN_LIMITS

    def _settled_usage(self, role: Role | None) -> Usage:
        """Settled usage of one role, or of all roles when ``role`` is ``None``."""
        if role is not None:
            ledger = self._ledgers.get(role)
            return ledger.usage if ledger is not None else _zero_usage()
        total = _zero_usage()
        for known in self._known_roles():
            total = _add_usage(total, self._settled_usage(known))
        return total

    def _reserved_usage(self, role: Role | None) -> Usage:
        """Estimated spend of the still-open reservations of a role or of the run."""
        total = _zero_usage()
        for reservation in self._reservations.values():
            if reservation.settled or (role is not None and reservation.role is not role):
                continue
            total = _add_usage(total, reservation.estimated)
        return total

    def _effective_usage(self, role: Role | None) -> Usage:
        """Spend the limits are compared against: settled plus reserved (FR-018)."""
        return _add_usage(self._settled_usage(role), self._reserved_usage(role))

    def _calls_for(self, role: Role) -> int:
        ledger = self._ledgers.get(role)
        return ledger.calls if ledger is not None else 0

    def _unsettled_reservation(self, role: Role) -> Reservation | None:
        return next(
            (
                reservation
                for reservation in self._reservations.values()
                if reservation.role is role and not reservation.settled
            ),
            None,
        )

    def _known_roles(self) -> tuple[Role, ...]:
        """Roles the ledger knows: spend, a reservation or a configured allowance."""
        roles = (
            set(self._ledgers)
            | {reservation.role for reservation in self._reservations.values()}
            | {allowance.role for allowance in self._policy.role_allowances}
        )
        return tuple(sorted(roles, key=lambda role: role.value))


def _reservation_id(role: Role, key: str) -> str:
    """Deterministic reservation id: the same logical call always addresses the same one."""
    return f"rsv_{role.value}_{key}"


def _tokens(usage: Usage) -> int:
    """Token spend of a usage; an unreported total falls back to its reported parts."""
    if usage.total_tokens is not None:
        return usage.total_tokens
    return usage.prompt_tokens + usage.completion_tokens


def _cost(usage: Usage) -> Decimal:
    """Cost spend of a usage; an unreported cost counts as zero, not as unknown."""
    return usage.cost if usage.cost is not None else Decimal("0")


def _add_usage(left: Usage, right: Usage) -> Usage:
    """Sum of two usages; the parts of a usage without a total fall back to the fold."""
    return Usage(
        prompt_tokens=left.prompt_tokens + right.prompt_tokens,
        completion_tokens=left.completion_tokens + right.completion_tokens,
        total_tokens=_tokens(left) + _tokens(right),
        cost=_add_cost(left.cost, right.cost),
    )


def _add_cost(left: Decimal | None, right: Decimal | None) -> Decimal | None:
    """Sum of two optional costs; an unknown cost contributes nothing.

    The same rule as the run usage summary (``execution/runs/store.py``): a
    cost nobody reported stays out of the total instead of becoming zero spend
    presented as fact.
    """
    if left is None:
        return right
    if right is None:
        return left
    return left + right
