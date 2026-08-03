"""Small in-memory repository for local-disabled auth and isolated policy tests."""

from __future__ import annotations

from collections.abc import Sequence
from datetime import datetime
from uuid import UUID

from hiveblot_contracts import (
    ArtifactVisibility,
    AuditEventRecord,
    AuthenticatedPrincipal,
    OrganizationMembership,
    OrganizationRecord,
    ResourceScope,
    UserRecord,
)


class InMemoryAuthorizationRepository:
    def __init__(self) -> None:
        self.principals: dict[str, AuthenticatedPrincipal] = {}
        self.scopes: dict[tuple[str, UUID], ResourceScope] = {}
        self.audit_events: list[AuditEventRecord] = []
        self.users: dict[UUID, UserRecord] = {}
        self.organizations: dict[UUID, OrganizationRecord] = {}
        self.memberships: dict[tuple[UUID, UUID], OrganizationMembership] = {}

    def authenticate_token(
        self,
        token_digest: str,
        *,
        authenticated_at: datetime,
    ) -> AuthenticatedPrincipal | None:
        principal = self.principals.get(token_digest)
        if principal is None:
            return None
        return principal.model_copy(update={"authenticated_at": authenticated_at})

    def resolve_scope(self, target_type: str, target_id: UUID) -> ResourceScope | None:
        return self.scopes.get(
            (target_type, target_id),
            ResourceScope(visibility=ArtifactVisibility.PUBLIC),
        )

    def append_audit_event(self, event: AuditEventRecord) -> AuditEventRecord:
        self.audit_events.append(event)
        return event

    def list_audit_events(
        self,
        *,
        organization_id: UUID,
        limit: int,
    ) -> Sequence[AuditEventRecord]:
        values = [
            event
            for event in reversed(self.audit_events)
            if event.organization_id == organization_id
        ]
        return tuple(values[:limit])

    def create_user(self, record: UserRecord) -> UserRecord:
        self.users[record.user_id] = record
        return record

    def create_organization(
        self,
        organization: OrganizationRecord,
        administrator: OrganizationMembership,
    ) -> OrganizationRecord:
        self.organizations[organization.organization_id] = organization
        self.memberships[(administrator.organization_id, administrator.user_id)] = administrator
        return organization

    def set_membership(self, membership: OrganizationMembership) -> OrganizationMembership:
        key = (membership.organization_id, membership.user_id)
        current = self.memberships.get(key)
        stored = membership
        if current is not None:
            stored = membership.model_copy(
                update={
                    "membership_id": current.membership_id,
                    "created_at": current.created_at,
                }
            )
        self.memberships[key] = stored
        return stored
