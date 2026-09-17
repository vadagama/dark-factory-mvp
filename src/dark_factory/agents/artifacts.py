"""Artifact and source kinds exchanged between agent roles (T-011).

Values are stable wire strings (ADR-015 p.3): profiles and skills declare
their inputs/outputs by these kinds in serialized manifests, so renaming a
value is a breaking change.
"""

from enum import StrEnum


class ArtifactKind(StrEnum):
    """Kind of a source or artifact a profile/skill declares as input or output."""

    TASK = "task"
    REQUIREMENTS = "requirements"
    SPEC = "spec"
    CHANGE_REQUEST = "change_request"
    CODE = "code"
    REVIEW_REPORT = "review_report"
    ACCEPTANCE_VERDICT = "acceptance_verdict"
    CONTEXT = "context"
    UX_SPEC = "ux_spec"
    ARCHITECTURE_REVIEW = "architecture_review"
    ADR_PROPOSAL = "adr_proposal"
    INFRASTRUCTURE_CHANGE = "infrastructure_change"
    SECURITY_REVIEW = "security_review"
    PIPELINE_CONFIG = "pipeline_config"
    OPS_VERDICT = "ops_verdict"
