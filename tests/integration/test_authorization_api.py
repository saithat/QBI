from __future__ import annotations

from datetime import UTC, datetime
from uuid import UUID, uuid4

import httpx
import pytest
from hiveblot_auth import AuthorizationService, InMemoryAuthorizationRepository, token_digest
from hiveblot_contracts import (
    ArtifactAcquisitionMethod,
    ArtifactRecord,
    ArtifactVisibility,
    AuthenticatedPrincipal,
    OrganizationMembership,
    OrganizationRole,
    ResourceScope,
)
from hiveblot_storage import ArtifactService, SourceAdapterRegistry

from apps.api.artifact_dependencies import get_artifact_service
from apps.api.auth_dependencies import get_authorization_service
from apps.api.main import app
from hiveblot.settings import get_settings
from tests.fakes.artifacts import InMemoryArtifactRepository, InMemoryObjectStore

NOW = datetime(2026, 8, 2, 18, tzinfo=UTC)
PEPPER = "p" * 32


@pytest.mark.asyncio
async def test_private_artifact_direct_access_and_mutation_are_tenant_isolated(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    organization_a = uuid4()
    organization_b = uuid4()
    token_a = "hvb_test_" + "a" * 40
    token_b = "hvb_test_" + "b" * 40
    authorization_repository = InMemoryAuthorizationRepository()
    authorization_repository.principals[token_digest(token_a, pepper=PEPPER.encode())] = _principal(
        organization_a
    )
    authorization_repository.principals[token_digest(token_b, pepper=PEPPER.encode())] = _principal(
        organization_b
    )
    authorization = AuthorizationService(
        authorization_repository,
        token_pepper=PEPPER,
        clock=lambda: NOW,
    )

    artifact_repository = InMemoryArtifactRepository()
    public = _artifact(ArtifactVisibility.PUBLIC)
    private_a = _artifact(
        ArtifactVisibility.ORGANIZATION_PRIVATE,
        organization_id=organization_a,
    )
    private_b = _artifact(
        ArtifactVisibility.ORGANIZATION_PRIVATE,
        organization_id=organization_b,
    )
    for record in (public, private_a, private_b):
        artifact_repository.artifacts[record.artifact_id] = record
        artifact_repository.storage_keys[record.artifact_id] = f"objects/{record.sha256}"
        authorization_repository.scopes[("artifact", record.artifact_id)] = ResourceScope(
            visibility=record.visibility,
            organization_id=record.organization_id,
        )
    artifacts = ArtifactService(
        repository=artifact_repository,
        object_store=InMemoryObjectStore(),
        source_adapters=SourceAdapterRegistry({}),
        max_bytes=1_000_000,
        upload_url_seconds=60,
        download_url_seconds=60,
        clock=lambda: NOW,
    )

    monkeypatch.setenv("AUTHENTICATION_MODE", "bearer")
    monkeypatch.setenv("AUTH_TOKEN_PEPPER", PEPPER)
    get_settings.cache_clear()
    app.dependency_overrides[get_authorization_service] = lambda: authorization
    app.dependency_overrides[get_artifact_service] = lambda: artifacts
    transport = httpx.ASGITransport(app=app)
    try:
        async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
            unauthenticated = await client.get(f"/api/v1/artifacts/{public.artifact_id}")
            public_a = await client.get(
                f"/api/v1/artifacts/{public.artifact_id}",
                headers=_bearer(token_a),
            )
            own_private = await client.get(
                f"/api/v1/artifacts/{private_a.artifact_id}",
                headers=_bearer(token_a),
            )
            cross_tenant = await client.get(
                f"/api/v1/artifacts/{private_a.artifact_id}",
                headers=_bearer(token_b),
            )
            other_direction = await client.get(
                f"/api/v1/artifacts/{private_b.artifact_id}",
                headers=_bearer(token_a),
            )
            forbidden_upload = await client.post(
                "/api/v1/artifact-uploads",
                headers=_bearer(token_b),
                json={
                    "schema_version": "1.0",
                    "original_filename": "wrong-tenant.png",
                    "declared_media_type": "image/png",
                    "expected_byte_size": 10,
                    "part_count": 1,
                    "visibility": "organization_private",
                    "organization_id": str(organization_a),
                    "relationships": [],
                },
            )

        assert unauthenticated.status_code == 401
        assert public_a.status_code == 200
        assert own_private.status_code == 200
        assert cross_tenant.status_code == 404
        assert other_direction.status_code == 404
        assert forbidden_upload.status_code == 403
        denied = [
            event for event in authorization_repository.audit_events if event.outcome == "denied"
        ]
        assert len(denied) == 3
        assert {event.organization_id for event in denied} == {organization_a, organization_b}
    finally:
        app.dependency_overrides.pop(get_authorization_service, None)
        app.dependency_overrides.pop(get_artifact_service, None)
        get_settings.cache_clear()


def _principal(organization_id: UUID) -> AuthenticatedPrincipal:
    user_id = uuid4()
    return AuthenticatedPrincipal(
        user_id=user_id,
        memberships=(
            OrganizationMembership(
                membership_id=uuid4(),
                organization_id=organization_id,
                user_id=user_id,
                role=OrganizationRole.SCIENTIST,
                active=True,
                created_at=NOW,
                updated_at=NOW,
            ),
        ),
        authenticated_at=NOW,
    )


def _artifact(
    visibility: ArtifactVisibility,
    *,
    organization_id: UUID | None = None,
) -> ArtifactRecord:
    artifact_id = uuid4()
    return ArtifactRecord(
        artifact_id=artifact_id,
        sha256=artifact_id.hex * 2,
        media_type="image/png",
        byte_size=10,
        original_filename=f"{artifact_id}.png",
        acquisition_method=ArtifactAcquisitionMethod.USER_UPLOAD,
        visibility=visibility,
        organization_id=organization_id,
        created_at=NOW,
    )


def _bearer(token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}"}
