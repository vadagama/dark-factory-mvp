"""Enumerations of the change domain (T-003).

Values are stable wire strings: they are serialized into run records and
persisted between CI jobs, so renaming a value is a breaking schema change
(versioned contracts, ADR-015 p.3).
"""

from enum import StrEnum


class Provider(StrEnum):
    """Source control / CI provider of a repository (ADR-019)."""

    GITLAB = "gitlab"
    GITHUB = "github"


class Route(StrEnum):
    """Factory Flow route of a run (ADR-005, ADR-023 p.6).

    All four routes traverse the same five stages; they differ in gate policy
    (``orchestration/rules/gates.py``) and in the risk-class band they allow
    (``orchestration/routes.py``).
    """

    QUICK = "quick"
    STANDARD = "standard"
    ARCHITECTURE = "architecture"
    FOUNDATION = "foundation"


class RiskClass(StrEnum):
    """Risk class of a change; drives gates and human control points."""

    R0 = "R0"
    R1 = "R1"
    R2 = "R2"
    R3 = "R3"
    R4 = "R4"


class BoundaryArea(StrEnum):
    """Protected architecture boundary of a change (ADR-018 p.5)."""

    PUBLIC_API = "public_api"
    DATA_SCHEMA = "data_schema"
    IAM = "iam"
    ARCHITECTURE_BOUNDARY = "architecture_boundary"


class Role(StrEnum):
    """Agent role catalog (ADR-007)."""

    PRODUCT = "product"
    DESIGN = "design"
    ARCHITECT = "architect"
    INFRASTRUCTURE = "infrastructure"
    SECURITY = "security"
    DEVELOP = "develop"
    QUALITY = "quality"
    CI_CD = "ci_cd"
    OPERATION = "operation"


class Stage(StrEnum):
    """Factory Flow stages (T-004).

    Specification -> Planning -> Construction -> Review/Verification -> Release.
    """

    SPECIFICATION = "specification"
    PLANNING = "planning"
    CONSTRUCTION = "construction"
    REVIEW_VERIFICATION = "review_verification"
    RELEASE = "release"


class Gate(StrEnum):
    """MVP gates (vision 3.9): Specification, Planning, Code, UI, Review, Verification, Release."""

    SPECIFICATION = "specification"
    PLANNING = "planning"
    CODE = "code"
    UI = "ui"
    REVIEW = "review"
    VERIFICATION = "verification"
    RELEASE = "release"


class ControlPoint(StrEnum):
    """Named human decision mandatory from a risk class on (ADR-023 p.1/p.4).

    A control point is not a gate (``Gate`` stays unextended): it binds one
    human decision to an existing ``(stage, gate)`` pair.
    """

    PROBLEM = "problem"
    SOLUTION = "solution"
    UX = "ux"
    DISCOVERY_RELEASE = "discovery_release"


class RunStatus(StrEnum):
    """Lifecycle status of a ChangeRun (execution)."""

    PENDING = "pending"
    RUNNING = "running"
    WAITING = "waiting"
    BLOCKED = "blocked"
    SUCCEEDED = "succeeded"
    FAILED = "failed"
    CANCELED = "canceled"
    SUPERSEDED = "superseded"


class StageStatus(StrEnum):
    """Lifecycle status of a StageRun."""

    PENDING = "pending"
    IN_PROGRESS = "in_progress"
    WAITING = "waiting"
    SUCCEEDED = "succeeded"
    FAILED = "failed"
    BLOCKED = "blocked"
    SKIPPED = "skipped"
    SUPERSEDED = "superseded"
    CANCELED = "canceled"


class FindingSeverity(StrEnum):
    """Severity of a review finding."""

    BLOCKER = "blocker"
    MAJOR = "major"
    MINOR = "minor"
    INFO = "info"


class FindingStatus(StrEnum):
    """Lifecycle status of a finding."""

    OPEN = "open"
    RESOLVED = "resolved"
    WAIVED = "waived"
    OBSOLETE = "obsolete"


class FindingOrigin(StrEnum):
    """Who produced a finding."""

    AGENT = "agent"
    HUMAN = "human"
    CI = "ci"


class GateStatus(StrEnum):
    """Result status of one gate."""

    PENDING = "pending"
    PASSED = "passed"
    FAILED = "failed"
    SKIPPED = "skipped"


class ReleaseStatus(StrEnum):
    """Outcome of the release verification of a change (US5, T034, ADR-011 p.6).

    ``released`` requires the unchanged expected digest (FR-011), a synced and
    healthy Argo Application (ADR-010) and a passing smoke check (FR-013).
    Anything else — including missing data — is ``release_failed``: the check
    order is fail-closed, and a failure carries the rollback signal (revert
    the GitOps commit, ADR-010/ADR-011 p.6).
    """

    RELEASED = "released"
    RELEASE_FAILED = "release_failed"


class DecisionOutcome(StrEnum):
    """Outcome of an approval decision."""

    APPROVED = "approved"
    REJECTED = "rejected"
    WAIVED = "waived"


class DecisionSource(StrEnum):
    """Who made an approval decision (ADR-018)."""

    HUMAN = "human"
    POLICY = "policy"
    AGENT = "agent"


class DecisionClass(StrEnum):
    """Class of an architectural decision (ADR-018 p.6)."""

    KNOWN_PATH = "known_path"
    BOUNDED_CHOICE = "bounded_choice"
    NEW_PATH = "new_path"


class HumanParticipation(StrEnum):
    """Human participation mode of a lifecycle phase (ADR-018 p.1)."""

    IN_THE_LOOP = "human_in_the_loop"
    ON_THE_LOOP = "human_on_the_loop"
    OFF_THE_LOOP = "human_off_the_loop"


class ChangeSource(StrEnum):
    """Intake source of a change."""

    TRACKER = "tracker"
    CONSOLE = "console"
    CLI = "cli"
    API = "api"


class ChangeRequestStatus(StrEnum):
    """Status of a change request in core terms (ADR-019 p.2)."""

    DRAFT = "draft"
    OPEN = "open"
    MERGED = "merged"
    CLOSED = "closed"


class ProductStatus(StrEnum):
    """Readiness of a product repository (ADR-030 p.1).

    ``created -> validating -> ready | error``: a failure keeps its cause in
    ``Product.status_reason``, never in the status value, so the wire value
    stays stable (ADR-030 p.4).
    """

    CREATED = "created"
    VALIDATING = "validating"
    READY = "ready"
    ERROR = "error"


class EvidenceType(StrEnum):
    """Kind of evidence attached to a result."""

    LOG = "log"
    REPORT = "report"
    SCREENSHOT = "screenshot"
    SBOM = "sbom"
    SPEC = "spec"
    DIFF = "diff"
    TEST_RESULTS = "test_results"
    DEPLOYMENT = "deployment"
    SMOKE = "smoke"
    OTHER = "other"


class StopOutcome(StrEnum):
    """Terminal outcome carried by NextAction.stop (stop conditions, T-004)."""

    BLOCKED = "blocked"
    FAILED = "failed"
    CANCELED = "canceled"


class EscalationRule(StrEnum):
    """Machine-checkable escalation condition of autonomous implementation (ADR-018 p.5, T-016).

    ``implementation_contract_unapproved`` is the T-016 precondition gate: an
    unapproved contract never enters implementation. The rest are the ADR-018
    p.5 conditions; ``risk_raised_to_r2`` is the machine-checked R2+ obligation
    gate (T-080, ADR-023 p.5) — no condition is a manual assessment any more.
    """

    IMPLEMENTATION_CONTRACT_UNAPPROVED = "implementation_contract_unapproved"
    REQUIREMENTS_DEFICIENT = "requirements_deficient"
    SCOPE_EXIT = "scope_exit"
    BOUNDARY_CHANGE = "boundary_change"
    NEW_ADR_PROPOSAL = "new_adr_proposal"
    RISK_RAISED_TO_R2 = "risk_raised_to_r2"
    UNRECOVERABLE_GATE_FAILURE = "unrecoverable_gate_failure"
    AUTONOMY_BUDGET_EXHAUSTED = "autonomy_budget_exhausted"
    UI_UNVERIFIABLE = "ui_unverifiable"
    IRREVERSIBLE_OPERATION = "irreversible_operation"
