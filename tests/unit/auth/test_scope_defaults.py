from datetime import UTC, datetime
from uuid import UUID, uuid4

import pytest
from fastapi import HTTPException
from hiveblot_auth import AuthorizationService, InMemoryAuthorizationRepository
from hiveblot_contracts import (
    ArtifactVisibility,
    AuthenticatedPrincipal,
    AuthorizationPermission,
    OrganizationMembership,
    OrganizationRole,
    ResourceScope,
)

from apps.api.authorization import private_creation_scope

NOW = datetime(2026, 8, 2, 20, tzinfo=UTC)


def test_single_organization_mutations_are_private_by_default() -> None:
    organization_id = uuid4()
    service = AuthorizationService(
        InMemoryAuthorizationRepository(),
        token_pepper="p" * 32,
        clock=lambda: NOW,
    )
    principal = _principal((organization_id,))

    scope = private_creation_scope(
        service,
        principal,
        case_scope=ResourceScope(visibility=ArtifactVisibility.PUBLIC),
        requested_visibility=None,
        requested_organization_id=None,
        permission=AuthorizationPermission.ANNOTATION_WRITE,
        resource_name="annotations",
    )

    assert scope.visibility is ArtifactVisibility.ORGANIZATION_PRIVATE
    assert scope.organization_id == organization_id


def test_multi_organization_mutations_require_an_explicit_scope() -> None:
    service = AuthorizationService(
        InMemoryAuthorizationRepository(),
        token_pepper="p" * 32,
        clock=lambda: NOW,
    )
    principal = _principal((uuid4(), uuid4()))

    with pytest.raises(HTTPException) as raised:
        private_creation_scope(
            service,
            principal,
            case_scope=ResourceScope(visibility=ArtifactVisibility.PUBLIC),
            requested_visibility=None,
            requested_organization_id=None,
            permission=AuthorizationPermission.JOB_SUBMIT,
            resource_name="jobs",
        )

    assert raised.value.status_code == 422


def _principal(organization_ids: tuple[UUID, ...]) -> AuthenticatedPrincipal:
    user_id = uuid4()
    return AuthenticatedPrincipal(
        user_id=user_id,
        memberships=tuple(
            OrganizationMembership(
                membership_id=uuid4(),
                organization_id=organization_id,
                user_id=user_id,
                role=OrganizationRole.SCIENTIST,
                active=True,
                created_at=NOW,
                updated_at=NOW,
            )
            for organization_id in organization_ids
        ),
        authenticated_at=NOW,
    )
