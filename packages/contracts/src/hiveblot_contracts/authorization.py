"""Organization, principal, permission, resource-scope, and audit contracts."""

from __future__ import annotations

from enum import StrEnum
from typing import Self
from uuid import UUID

from pydantic import AwareDatetime, Field, model_validator

from .artifacts import ArtifactVisibility
from .base import ContractModel, Identifier

PLATFORM_OPERATOR_USER_ID = UUID("00000000-0000-0000-0000-000000000001")


class UserStatus(StrEnum):
    ACTIVE = "active"
    DISABLED = "disabled"


class OrganizationStatus(StrEnum):
    ACTIVE = "active"
    SUSPENDED = "suspended"


class OrganizationRole(StrEnum):
    ADMINISTRATOR = "organization_administrator"
    SCIENTIST = "scientist"
    REVIEWER = "reviewer"
    READ_ONLY = "read_only"


class AuthorizationPermission(StrEnum):
    ARTIFACT_READ = "artifact.read"
    ARTIFACT_WRITE = "artifact.write"
    EVALUATION_READ = "evaluation.read"
    EVALUATION_REVIEW = "evaluation.review"
    EVALUATION_MANAGE = "evaluation.manage"
    ANNOTATION_READ = "annotation.read"
    ANNOTATION_WRITE = "annotation.write"
    TRACE_READ = "trace.read"
    JOB_READ = "job.read"
    JOB_SUBMIT = "job.submit"
    JOB_CANCEL = "job.cancel"
    DATASET_READ = "dataset.read"
    DATASET_PUBLISH = "dataset.publish"
    ORGANIZATION_MANAGE = "organization.manage"
    AUDIT_READ = "audit.read"
    SEARCH = "search.execute"
    PLATFORM_SEARCH_MANAGE = "platform.search.manage"


class UserRecord(ContractModel):
    user_id: UUID
    email: str = Field(min_length=3, max_length=320)
    display_name: str = Field(min_length=1, max_length=200)
    status: UserStatus
    created_at: AwareDatetime
    updated_at: AwareDatetime

    @model_validator(mode="after")
    def timestamps_are_ordered(self) -> Self:
        if self.updated_at < self.created_at:
            raise ValueError("user updated_at cannot precede created_at")
        return self


class OrganizationRecord(ContractModel):
    organization_id: UUID
    slug: Identifier
    display_name: str = Field(min_length=1, max_length=200)
    status: OrganizationStatus
    created_at: AwareDatetime
    updated_at: AwareDatetime

    @model_validator(mode="after")
    def timestamps_are_ordered(self) -> Self:
        if self.updated_at < self.created_at:
            raise ValueError("organization updated_at cannot precede created_at")
        return self


class OrganizationMembership(ContractModel):
    membership_id: UUID
    organization_id: UUID
    user_id: UUID
    role: OrganizationRole
    active: bool
    created_at: AwareDatetime
    updated_at: AwareDatetime

    @model_validator(mode="after")
    def timestamps_are_ordered(self) -> Self:
        if self.updated_at < self.created_at:
            raise ValueError("membership updated_at cannot precede created_at")
        return self


class AuthenticatedPrincipal(ContractModel):
    user_id: UUID
    token_id: UUID | None = None
    memberships: tuple[OrganizationMembership, ...] = ()
    authenticated_at: AwareDatetime
    system: bool = False
    platform_operator: bool = False

    @model_validator(mode="after")
    def membership_identities_are_unique(self) -> Self:
        organizations = [item.organization_id for item in self.memberships if item.active]
        if len(organizations) != len(set(organizations)):
            raise ValueError("a principal cannot have multiple active roles in one organization")
        if self.system:
            if self.user_id != PLATFORM_OPERATOR_USER_ID:
                raise ValueError("system principals must use the reserved platform identity")
            if self.token_id is not None:
                raise ValueError("system principals cannot carry an API token identifier")
        if self.platform_operator:
            if self.user_id != PLATFORM_OPERATOR_USER_ID:
                raise ValueError("platform operators must use the reserved platform identity")
            if self.token_id is None:
                raise ValueError("platform operators require an authenticated API token")
        if self.system and self.platform_operator:
            raise ValueError("a principal cannot be both system and platform operator")
        if (self.system or self.platform_operator) and self.memberships:
            raise ValueError("platform principals cannot carry organization memberships")
        return self


class ResourceScope(ContractModel):
    visibility: ArtifactVisibility
    organization_id: UUID | None = None

    @model_validator(mode="after")
    def visibility_matches_organization(self) -> Self:
        if self.visibility is ArtifactVisibility.PUBLIC and self.organization_id is not None:
            raise ValueError("public resources cannot belong to an organization")
        if (
            self.visibility is ArtifactVisibility.ORGANIZATION_PRIVATE
            and self.organization_id is None
        ):
            raise ValueError("organization-private resources require an organization")
        return self


class AuditOutcome(StrEnum):
    ALLOWED = "allowed"
    DENIED = "denied"


class AuditEventRecord(ContractModel):
    audit_event_id: UUID
    actor_user_id: UUID
    token_id: UUID | None = None
    action: Identifier
    outcome: AuditOutcome
    target_type: Identifier
    target_id: UUID | None = None
    organization_id: UUID | None = None
    request_id: UUID
    reason: str | None = Field(default=None, min_length=1, max_length=1000)
    occurred_at: AwareDatetime
