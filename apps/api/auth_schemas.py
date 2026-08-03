"""HTTP adapters for organization administration and audit inspection."""

from __future__ import annotations

from datetime import datetime
from typing import Literal
from uuid import UUID

from hiveblot_contracts import ContractModel
from pydantic import Field

from .evaluation_schemas import JsonUUID


class MembershipResponse(ContractModel):
    membership_id: UUID
    organization_id: UUID
    user_id: UUID
    role: Literal["organization_administrator", "scientist", "reviewer", "read_only"]
    active: bool
    created_at: datetime
    updated_at: datetime


class CurrentPrincipalResponse(ContractModel):
    user_id: UUID
    token_id: UUID | None
    memberships: tuple[MembershipResponse, ...]


class CreateOrganizationRequest(ContractModel):
    slug: str = Field(pattern=r"^[a-z0-9][a-z0-9_-]{0,199}$")
    display_name: str = Field(min_length=1, max_length=200)


class OrganizationResponse(ContractModel):
    organization_id: UUID
    slug: str
    display_name: str
    status: Literal["active", "suspended"]
    created_at: datetime
    updated_at: datetime


class SetMembershipRequest(ContractModel):
    role: Literal["organization_administrator", "scientist", "reviewer", "read_only"]
    active: bool = True


class AuditEventResponse(ContractModel):
    audit_event_id: UUID
    actor_user_id: UUID
    token_id: UUID | None
    action: str
    outcome: Literal["allowed", "denied"]
    target_type: str
    target_id: UUID | None
    organization_id: UUID | None
    request_id: UUID
    reason: str | None
    occurred_at: datetime


class AuditEventListResponse(ContractModel):
    organization_id: JsonUUID
    events: tuple[AuditEventResponse, ...]
