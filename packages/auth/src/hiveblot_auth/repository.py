"""Persistence boundary for identities, memberships, scopes, tokens, and audit events."""

from __future__ import annotations

from collections.abc import Sequence
from datetime import datetime
from typing import Protocol
from uuid import UUID

from hiveblot_contracts import (
    AuditEventRecord,
    AuthenticatedPrincipal,
    OrganizationMembership,
    OrganizationRecord,
    ResourceScope,
    UserRecord,
)


class AuthorizationRepository(Protocol):
    def authenticate_token(
        self,
        token_digest: str,
        *,
        authenticated_at: datetime,
    ) -> AuthenticatedPrincipal | None: ...

    def resolve_scope(self, target_type: str, target_id: UUID) -> ResourceScope | None: ...

    def append_audit_event(self, event: AuditEventRecord) -> AuditEventRecord: ...

    def list_audit_events(
        self,
        *,
        organization_id: UUID,
        limit: int,
    ) -> Sequence[AuditEventRecord]: ...

    def create_user(self, record: UserRecord) -> UserRecord: ...

    def create_organization(
        self,
        organization: OrganizationRecord,
        administrator: OrganizationMembership,
    ) -> OrganizationRecord: ...

    def set_membership(self, membership: OrganizationMembership) -> OrganizationMembership: ...
