from datetime import UTC, datetime
from uuid import uuid4

import pytest
from hiveblot_contracts import (
    ArtifactVisibility,
    AuthenticatedPrincipal,
    OrganizationMembership,
    OrganizationRole,
    ResourceScope,
)
from pydantic import ValidationError

NOW = datetime(2026, 8, 2, 20, tzinfo=UTC)


def test_authorization_contracts_are_strict_and_require_complete_private_scope() -> None:
    with pytest.raises(ValidationError, match="Extra inputs are not permitted"):
        ResourceScope.model_validate(
            {
                "schema_version": "1.0",
                "visibility": "public",
                "unexpected": True,
            }
        )
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
