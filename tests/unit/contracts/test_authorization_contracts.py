from datetime import UTC, datetime
from uuid import uuid4

import pytest
from hiveblot_contracts import (
    PLATFORM_OPERATOR_USER_ID,
    ArtifactVisibility,
    AuthenticatedPrincipal,
    OrganizationMembership,
    OrganizationRole,
    ResourceScope,
)
from pydantic import ValidationError

NOW = datetime(2026, 8, 2, 20, tzinfo=UTC)


def test_resource_visibility_requires_a_consistent_organization_scope() -> None:
    with pytest.raises(ValidationError, match="require an organization"):
        ResourceScope(visibility=ArtifactVisibility.ORGANIZATION_PRIVATE)
    with pytest.raises(ValidationError, match="cannot belong to an organization"):
        ResourceScope(
            visibility=ArtifactVisibility.PUBLIC,
            organization_id=uuid4(),
        )


def test_principal_rejects_duplicate_active_memberships_for_one_organization() -> None:
    user_id = uuid4()
    organization_id = uuid4()

    def membership(role: OrganizationRole) -> OrganizationMembership:
        return OrganizationMembership(
            membership_id=uuid4(),
            organization_id=organization_id,
            user_id=user_id,
            role=role,
            active=True,
            created_at=NOW,
            updated_at=NOW,
        )

    with pytest.raises(ValidationError, match="multiple active roles"):
        AuthenticatedPrincipal(
            user_id=user_id,
            memberships=(
                membership(OrganizationRole.SCIENTIST),
                membership(OrganizationRole.REVIEWER),
            ),
            authenticated_at=NOW,
        )


def test_platform_operator_requires_the_reserved_token_identity() -> None:
    token_id = uuid4()
    principal = AuthenticatedPrincipal(
        user_id=PLATFORM_OPERATOR_USER_ID,
        token_id=token_id,
        authenticated_at=NOW,
        platform_operator=True,
    )

    assert principal.platform_operator is True
    with pytest.raises(ValidationError, match="reserved platform identity"):
        AuthenticatedPrincipal(
            user_id=uuid4(),
            token_id=token_id,
            authenticated_at=NOW,
            platform_operator=True,
        )
    with pytest.raises(ValidationError, match="authenticated API token"):
        AuthenticatedPrincipal(
            user_id=PLATFORM_OPERATOR_USER_ID,
            authenticated_at=NOW,
            platform_operator=True,
        )
