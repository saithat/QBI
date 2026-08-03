from __future__ import annotations

from datetime import UTC, datetime
from uuid import UUID, uuid4

import pytest
from hiveblot_auth import AuthorizationService, InMemoryAuthorizationRepository, PermissionDenied
from hiveblot_contracts import (
    ArtifactVisibility,
    AuthenticatedPrincipal,
    AuthorizationPermission,
    OrganizationMembership,
    OrganizationRole,
    ResourceScope,
)

NOW = datetime(2026, 8, 2, 18, tzinfo=UTC)


def test_role_permissions_are_scope_aware_and_every_decision_is_audited() -> None:
    repository = InMemoryAuthorizationRepository()
    service = AuthorizationService(repository, token_pepper="p" * 32, clock=lambda: NOW)
    organization_a = uuid4()
    organization_b = uuid4()
    scientist = _principal(organization_a, OrganizationRole.SCIENTIST)
    read_only = _principal(organization_a, OrganizationRole.READ_ONLY)
    unaffiliated = AuthenticatedPrincipal(user_id=uuid4(), authenticated_at=NOW)
    public = ResourceScope(visibility=ArtifactVisibility.PUBLIC)
    private_a = ResourceScope(
        visibility=ArtifactVisibility.ORGANIZATION_PRIVATE,
        organization_id=organization_a,
    )
    private_b = ResourceScope(
        visibility=ArtifactVisibility.ORGANIZATION_PRIVATE,
        organization_id=organization_b,
    )

    service.authorize_scope(
        unaffiliated,
        AuthorizationPermission.ARTIFACT_READ,
        scope=public,
        target_type="artifact",
        request_id=uuid4(),
    )
    service.authorize_scope(
        scientist,
        AuthorizationPermission.ARTIFACT_WRITE,
        scope=private_a,
        target_type="artifact",
        request_id=uuid4(),
    )
    service.authorize_scope(
        read_only,
        AuthorizationPermission.ARTIFACT_READ,
        scope=private_a,
        target_type="artifact",
        request_id=uuid4(),
    )
    with pytest.raises(PermissionDenied):
        service.authorize_scope(
            scientist,
            AuthorizationPermission.ARTIFACT_READ,
            scope=private_b,
            target_type="artifact",
            request_id=uuid4(),
        )
    with pytest.raises(PermissionDenied):
        service.authorize_scope(
            read_only,
            AuthorizationPermission.ARTIFACT_WRITE,
            scope=private_a,
            target_type="artifact",
            request_id=uuid4(),
        )

    assert [event.outcome.value for event in repository.audit_events] == [
        "allowed",
        "allowed",
        "allowed",
        "denied",
        "denied",
    ]
    assert repository.audit_events[-2].organization_id == organization_b
    assert service.organizations_with_permission(
        scientist,
        AuthorizationPermission.JOB_SUBMIT,
    ) == (organization_a,)
    assert (
        service.organizations_with_permission(
            read_only,
            AuthorizationPermission.JOB_SUBMIT,
        )
        == ()
    )


def test_system_principal_is_reserved_for_explicit_auth_disabled_mode() -> None:
    repository = InMemoryAuthorizationRepository()
    service = AuthorizationService(repository, token_pepper="p" * 32, clock=lambda: NOW)
    principal = service.system_principal()

    assert principal.system is True
    assert (
        service.organizations_with_permission(
            principal,
            AuthorizationPermission.ORGANIZATION_MANAGE,
        )
        is None
    )
    service.authorize_scope(
        principal,
        AuthorizationPermission.ORGANIZATION_MANAGE,
        scope=ResourceScope(
            visibility=ArtifactVisibility.ORGANIZATION_PRIVATE,
            organization_id=uuid4(),
        ),
        target_type="organization",
        request_id=uuid4(),
    )


def _principal(
    organization_id: UUID,
    role: OrganizationRole,
) -> AuthenticatedPrincipal:
    user_id = uuid4()
    return AuthenticatedPrincipal(
        user_id=user_id,
        memberships=(
            OrganizationMembership(
                membership_id=uuid4(),
                organization_id=organization_id,
                user_id=user_id,
                role=role,
                active=True,
                created_at=NOW,
                updated_at=NOW,
            ),
        ),
        authenticated_at=NOW,
    )
