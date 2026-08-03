"""Backend permission evaluation, opaque-token authentication, and audit orchestration."""

from __future__ import annotations

import hashlib
import hmac
from collections.abc import Callable, Sequence
from datetime import UTC, datetime
from uuid import UUID, uuid4

from hiveblot_contracts import (
    PLATFORM_OPERATOR_USER_ID,
    ArtifactVisibility,
    AuditEventRecord,
    AuditOutcome,
    AuthenticatedPrincipal,
    AuthorizationPermission,
    OrganizationMembership,
    OrganizationRecord,
    OrganizationRole,
    OrganizationStatus,
    ResourceScope,
    UserRecord,
    UserStatus,
)

from .errors import (
    AuthorizationTargetNotFound,
    InvalidAuthorizationState,
    InvalidCredentials,
    PermissionDenied,
)
from .repository import AuthorizationRepository

_READ_PERMISSIONS = frozenset(
    {
        AuthorizationPermission.ARTIFACT_READ,
        AuthorizationPermission.EVALUATION_READ,
        AuthorizationPermission.ANNOTATION_READ,
        AuthorizationPermission.TRACE_READ,
        AuthorizationPermission.JOB_READ,
        AuthorizationPermission.DATASET_READ,
        AuthorizationPermission.SEARCH,
    }
)
_ROLE_PERMISSIONS: dict[OrganizationRole, frozenset[AuthorizationPermission]] = {
    OrganizationRole.ADMINISTRATOR: frozenset(
        permission
        for permission in AuthorizationPermission
        if permission is not AuthorizationPermission.PLATFORM_SEARCH_MANAGE
    ),
    OrganizationRole.SCIENTIST: frozenset(
        {
            *_READ_PERMISSIONS,
            AuthorizationPermission.ARTIFACT_WRITE,
            AuthorizationPermission.EVALUATION_REVIEW,
            AuthorizationPermission.EVALUATION_MANAGE,
            AuthorizationPermission.ANNOTATION_WRITE,
            AuthorizationPermission.JOB_SUBMIT,
            AuthorizationPermission.JOB_CANCEL,
            AuthorizationPermission.DATASET_PUBLISH,
        }
    ),
    OrganizationRole.REVIEWER: frozenset(
        {
            *_READ_PERMISSIONS,
            AuthorizationPermission.EVALUATION_REVIEW,
            AuthorizationPermission.ANNOTATION_WRITE,
        }
    ),
    OrganizationRole.READ_ONLY: _READ_PERMISSIONS,
}
SYSTEM_USER_ID = PLATFORM_OPERATOR_USER_ID


class AuthorizationService:
    def __init__(
        self,
        repository: AuthorizationRepository,
        *,
        token_pepper: str,
        clock: Callable[[], datetime] | None = None,
    ) -> None:
        self._repository = repository
        self._token_pepper = token_pepper.encode("utf-8")
        self._clock = clock or (lambda: datetime.now(UTC))

    def authenticate(self, token: str) -> AuthenticatedPrincipal:
        candidate = token.strip()
        if len(candidate) < 32 or len(candidate) > 4096:
            raise InvalidCredentials("invalid bearer token")
        principal = self._repository.authenticate_token(
            token_digest(candidate, pepper=self._token_pepper),
            authenticated_at=self._clock(),
        )
        if principal is None:
            raise InvalidCredentials("invalid bearer token")
        return principal

    def system_principal(self) -> AuthenticatedPrincipal:
        return AuthenticatedPrincipal(
            user_id=SYSTEM_USER_ID,
            authenticated_at=self._clock(),
            system=True,
        )

    def authorize_resource(
        self,
        principal: AuthenticatedPrincipal,
        permission: AuthorizationPermission,
        *,
        target_type: str,
        target_id: UUID,
        request_id: UUID,
    ) -> ResourceScope:
        scope = self._repository.resolve_scope(target_type, target_id)
        if scope is None:
            raise AuthorizationTargetNotFound(f"{target_type} {target_id} does not exist")
        self.authorize_scope(
            principal,
            permission,
            scope=scope,
            target_type=target_type,
            target_id=target_id,
            request_id=request_id,
        )
        return scope

    def authorize_scope(
        self,
        principal: AuthenticatedPrincipal,
        permission: AuthorizationPermission,
        *,
        scope: ResourceScope,
        target_type: str,
        request_id: UUID,
        target_id: UUID | None = None,
    ) -> None:
        allowed = self.is_allowed(principal, permission, scope=scope)
        event = AuditEventRecord(
            audit_event_id=uuid4(),
            actor_user_id=principal.user_id,
            token_id=principal.token_id,
            action=permission.value,
            outcome=AuditOutcome.ALLOWED if allowed else AuditOutcome.DENIED,
            target_type=target_type,
            target_id=target_id,
            organization_id=scope.organization_id,
            request_id=request_id,
            reason=None if allowed else "permission_or_scope_denied",
            occurred_at=self._clock(),
        )
        self._repository.append_audit_event(event)
        if not allowed:
            raise PermissionDenied("resource is not accessible")

    def is_allowed(
        self,
        principal: AuthenticatedPrincipal,
        permission: AuthorizationPermission,
        *,
        scope: ResourceScope,
    ) -> bool:
        if principal.system:
            return True
        if principal.platform_operator and permission in {
            AuthorizationPermission.PLATFORM_SEARCH_MANAGE,
            AuthorizationPermission.SEARCH,
        }:
            return True
        active = tuple(item for item in principal.memberships if item.active)
        if scope.visibility is ArtifactVisibility.PUBLIC:
            if permission in _READ_PERMISSIONS:
                return True
            return any(permission in _ROLE_PERMISSIONS[item.role] for item in active)
        return any(
            item.organization_id == scope.organization_id
            and permission in _ROLE_PERMISSIONS[item.role]
            for item in active
        )

    def organizations_with_permission(
        self,
        principal: AuthenticatedPrincipal,
        permission: AuthorizationPermission,
    ) -> tuple[UUID, ...] | None:
        if principal.system or (
            principal.platform_operator
            and permission
            in {
                AuthorizationPermission.PLATFORM_SEARCH_MANAGE,
                AuthorizationPermission.SEARCH,
            }
        ):
            return None
        return tuple(
            sorted(
                {
                    membership.organization_id
                    for membership in principal.memberships
                    if membership.active and permission in _ROLE_PERMISSIONS[membership.role]
                },
                key=str,
            )
        )

    def create_user(
        self,
        *,
        email: str,
        display_name: str,
    ) -> UserRecord:
        now = self._clock()
        return self._repository.create_user(
            UserRecord(
                user_id=uuid4(),
                email=email.strip().casefold(),
                display_name=display_name.strip(),
                status=UserStatus.ACTIVE,
                created_at=now,
                updated_at=now,
            )
        )

    def create_organization(
        self,
        principal: AuthenticatedPrincipal,
        *,
        slug: str,
        display_name: str,
        request_id: UUID,
    ) -> OrganizationRecord:
        if principal.system:
            raise InvalidAuthorizationState("system principal cannot own an organization")
        now = self._clock()
        organization = OrganizationRecord(
            organization_id=uuid4(),
            slug=slug,
            display_name=display_name.strip(),
            status=OrganizationStatus.ACTIVE,
            created_at=now,
            updated_at=now,
        )
        membership = OrganizationMembership(
            membership_id=uuid4(),
            organization_id=organization.organization_id,
            user_id=principal.user_id,
            role=OrganizationRole.ADMINISTRATOR,
            active=True,
            created_at=now,
            updated_at=now,
        )
        created = self._repository.create_organization(organization, membership)
        self._repository.append_audit_event(
            AuditEventRecord(
                audit_event_id=uuid4(),
                actor_user_id=principal.user_id,
                token_id=principal.token_id,
                action=AuthorizationPermission.ORGANIZATION_MANAGE.value,
                outcome=AuditOutcome.ALLOWED,
                target_type="organization",
                target_id=created.organization_id,
                organization_id=created.organization_id,
                request_id=request_id,
                reason="organization_created",
                occurred_at=self._clock(),
            )
        )
        return created

    def set_membership(
        self,
        principal: AuthenticatedPrincipal,
        *,
        organization_id: UUID,
        user_id: UUID,
        role: OrganizationRole,
        active: bool,
        request_id: UUID,
    ) -> OrganizationMembership:
        scope = ResourceScope(
            visibility=ArtifactVisibility.ORGANIZATION_PRIVATE,
            organization_id=organization_id,
        )
        self.authorize_scope(
            principal,
            AuthorizationPermission.ORGANIZATION_MANAGE,
            scope=scope,
            target_type="organization_membership",
            target_id=user_id,
            request_id=request_id,
        )
        if user_id == SYSTEM_USER_ID:
            raise InvalidAuthorizationState(
                "the reserved platform identity cannot join an organization"
            )
        now = self._clock()
        return self._repository.set_membership(
            OrganizationMembership(
                membership_id=uuid4(),
                organization_id=organization_id,
                user_id=user_id,
                role=role,
                active=active,
                created_at=now,
                updated_at=now,
            )
        )

    def list_audit_events(
        self,
        principal: AuthenticatedPrincipal,
        *,
        organization_id: UUID,
        request_id: UUID,
        limit: int = 100,
    ) -> Sequence[AuditEventRecord]:
        if not 1 <= limit <= 500:
            raise InvalidAuthorizationState("audit limit must be between 1 and 500")
        self.authorize_scope(
            principal,
            AuthorizationPermission.AUDIT_READ,
            scope=ResourceScope(
                visibility=ArtifactVisibility.ORGANIZATION_PRIVATE,
                organization_id=organization_id,
            ),
            target_type="audit_log",
            target_id=organization_id,
            request_id=request_id,
        )
        return self._repository.list_audit_events(
            organization_id=organization_id,
            limit=limit,
        )


def token_digest(token: str, *, pepper: bytes) -> str:
    """Return a deterministic keyed digest without persisting or logging the bearer token."""

    return hmac.new(pepper, token.encode("utf-8"), hashlib.sha256).hexdigest()
