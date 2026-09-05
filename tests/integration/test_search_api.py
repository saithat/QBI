from __future__ import annotations

from datetime import UTC, datetime
from uuid import UUID, uuid4

import httpx
import pytest
from hiveblot_auth import AuthorizationService, InMemoryAuthorizationRepository, token_digest
from hiveblot_contracts import (
    PLATFORM_OPERATOR_USER_ID,
    ArtifactVisibility,
    AuthenticatedPrincipal,
    EvidenceRelation,
    OrganizationMembership,
    OrganizationRole,
)
from hiveblot_search import (
    DEFAULT_EMBEDDING_MODEL,
    DEFAULT_RERANKER,
    DeterministicScientificEmbedder,
    EvidenceSearchService,
    InMemoryEvidenceSearchRepository,
    TokenOverlapReranker,
)

from apps.api.auth_dependencies import get_authorization_service
from apps.api.main import app
from apps.api.search_dependencies import get_evidence_search_service
from hiveblot.settings import get_settings
from tests.fakes.search import NOW, evidence_document

PEPPER = "p" * 32


@pytest.mark.asyncio
async def test_search_api_filters_tenants_and_preserves_exact_citations(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    organization_a = uuid4()
    organization_b = uuid4()
    public = evidence_document(
        statement="Public p53 western blot evidence.",
        relation=EvidenceRelation.CONTRADICTS,
    )
    private_a = evidence_document(
        statement="Organization A private p53 evidence.",
        visibility=ArtifactVisibility.ORGANIZATION_PRIVATE,
        organization_id=organization_a,
    )
    private_b = evidence_document(
        statement="Organization B private p53 evidence.",
        visibility=ArtifactVisibility.ORGANIZATION_PRIVATE,
        organization_id=organization_b,
    )
    search_repository = InMemoryEvidenceSearchRepository()
    search_service = _search_service(search_repository)
    configuration = search_service.create_configuration(
        index_name="western-blot-evidence",
        configuration_version="1.0.0",
        embedding_model=DEFAULT_EMBEDDING_MODEL,
        embedding_dimensions=256,
        lexical_weight=0.5,
        semantic_weight=0.5,
        candidate_limit=100,
        reranker=None,
        reranker_weight=0,
        created_by=uuid4(),
    )
    version = search_service.build_version(
        configuration_id=configuration.configuration_id,
        index_version="tenant-test",
        documents=(public, private_a, private_b),
        created_by=uuid4(),
    )
    _activate_for_public_query(search_service, version.index_version_id, public)

    token_a = "hvb_test_" + "a" * 40
    token_b = "hvb_test_" + "b" * 40
    authorization_repository = InMemoryAuthorizationRepository()
    authorization_repository.principals[token_digest(token_a, pepper=PEPPER.encode())] = _principal(
        organization_a
    )
    authorization_repository.principals[token_digest(token_b, pepper=PEPPER.encode())] = _principal(
        organization_b
    )
    authorization = AuthorizationService(authorization_repository, token_pepper=PEPPER)
    monkeypatch.setenv("AUTHENTICATION_MODE", "bearer")
    monkeypatch.setenv("AUTH_TOKEN_PEPPER", PEPPER)
    get_settings.cache_clear()
    app.dependency_overrides[get_authorization_service] = lambda: authorization
    app.dependency_overrides[get_evidence_search_service] = lambda: search_service
    transport = httpx.ASGITransport(app=app)
    try:
        async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
            unauthenticated = await client.post(
                "/api/v1/evidence-search", json={"query": "p53 evidence"}
            )
            result_a = await client.post(
                "/api/v1/evidence-search",
                headers={"Authorization": f"Bearer {token_a}"},
                json={"query": "p53 evidence"},
            )
            result_b = await client.post(
                "/api/v1/evidence-search",
                headers={"Authorization": f"Bearer {token_b}"},
                json={"query": "p53 evidence"},
            )
    finally:
        app.dependency_overrides.pop(get_authorization_service, None)
        app.dependency_overrides.pop(get_evidence_search_service, None)
        get_settings.cache_clear()

    assert unauthenticated.status_code == 401
    assert result_a.status_code == 200, result_a.text
    assert result_b.status_code == 200, result_b.text
    ids_a = {UUID(item["document"]["document_id"]) for item in result_a.json()["hits"]}
    ids_b = {UUID(item["document"]["document_id"]) for item in result_b.json()["hits"]}
    assert ids_a == {public.document_id, private_a.document_id}
    assert ids_b == {public.document_id, private_b.document_id}
    public_hit = next(
        hit
        for hit in result_a.json()["hits"]
        if hit["document"]["document_id"] == str(public.document_id)
    )
    citation = public.citations[0]
    returned_citation = public_hit["document"]["citations"][0]
    assert returned_citation["artifact_url"] == f"/api/v1/artifacts/{citation.artifact.artifact_id}"
    assert returned_citation["source_uri"] == citation.source_uri
    assert len(public_hit["contradictory_observations"]) == 1
    assert result_a.json()["trace_id"]


@pytest.mark.asyncio
async def test_platform_operator_token_is_required_for_index_management(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    operator_token = "hvb_platform_test_" + "s" * 40
    administrator_token = "hvb_admin_test_" + "a" * 40
    repository = InMemoryAuthorizationRepository()
    repository.principals[token_digest(operator_token, pepper=PEPPER.encode())] = (
        AuthenticatedPrincipal(
            user_id=PLATFORM_OPERATOR_USER_ID,
            token_id=uuid4(),
            authenticated_at=NOW,
            platform_operator=True,
        )
    )
    repository.principals[token_digest(administrator_token, pepper=PEPPER.encode())] = _principal(
        uuid4(), role=OrganizationRole.ADMINISTRATOR
    )
    authorization = AuthorizationService(repository, token_pepper=PEPPER)
    search_service = _search_service(InMemoryEvidenceSearchRepository())
    monkeypatch.setenv("AUTHENTICATION_MODE", "bearer")
    monkeypatch.setenv("AUTH_TOKEN_PEPPER", PEPPER)
    get_settings.cache_clear()
    app.dependency_overrides[get_authorization_service] = lambda: authorization
    app.dependency_overrides[get_evidence_search_service] = lambda: search_service
    transport = httpx.ASGITransport(app=app)
    try:
        async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
            denied = await client.post(
                "/api/v1/evidence-index-configurations",
                headers={"Authorization": f"Bearer {administrator_token}"},
                json=_configuration_payload(),
            )
            created = await client.post(
                "/api/v1/evidence-index-configurations",
                headers={"Authorization": f"Bearer {operator_token}"},
                json=_configuration_payload(),
            )
            identity = await client.get(
                "/api/v1/auth/me",
                headers={"Authorization": f"Bearer {operator_token}"},
            )
    finally:
        app.dependency_overrides.pop(get_authorization_service, None)
        app.dependency_overrides.pop(get_evidence_search_service, None)
        get_settings.cache_clear()

    assert denied.status_code == 403
    assert created.status_code == 201, created.text
    assert identity.json()["platform_operator"] is True
    assert identity.json()["system"] is False


def _configuration_payload() -> dict[str, object]:
    return {
        "schema_version": "1.0",
        "index_name": "western-blot-evidence",
        "configuration_version": "1.0.0",
        "embedding_model": {
            "schema_version": "1.0",
            "provider": DEFAULT_EMBEDDING_MODEL.provider,
            "name": DEFAULT_EMBEDDING_MODEL.name,
            "version": DEFAULT_EMBEDDING_MODEL.version,
        },
        "embedding_dimensions": 256,
        "lexical_weight": 0.5,
        "semantic_weight": 0.5,
        "candidate_limit": 100,
        "reranker": {
            "schema_version": "1.0",
            "name": DEFAULT_RERANKER.name,
            "version": DEFAULT_RERANKER.version,
        },
        "reranker_weight": 0.15,
    }


def _search_service(repository: InMemoryEvidenceSearchRepository) -> EvidenceSearchService:
    return EvidenceSearchService(
        repository,
        embedder=DeterministicScientificEmbedder(dimensions=256),
        reranker=TokenOverlapReranker(),
        default_index_name="western-blot-evidence",
        clock=lambda: NOW,
    )


def _principal(
    organization_id: UUID,
    *,
    role: OrganizationRole = OrganizationRole.SCIENTIST,
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
        authenticated_at=datetime(2026, 8, 2, 22, tzinfo=UTC),
    )


def _activate_for_public_query(
    service: EvidenceSearchService,
    index_version_id: UUID,
    public: object,
) -> None:
    from hiveblot_contracts import (
        EvidenceIndexDocument,
        RetrievalEvaluationConfiguration,
        RetrievalEvaluationQuery,
        RetrievalRelevanceJudgment,
    )

    assert isinstance(public, EvidenceIndexDocument)
    dataset = service.freeze_evaluation_dataset(
        dataset_name="tenant-leakage-gold",
        dataset_version="1.0.0",
        visibility=ArtifactVisibility.PUBLIC,
        organization_id=None,
        queries=(
            RetrievalEvaluationQuery(
                query_id="public-p53",
                query="public p53 evidence",
                relevant=(
                    RetrievalRelevanceJudgment(
                        document_id=public.document_id,
                        relevance=3,
                        required_citation_artifact_ids=(public.citations[0].artifact.artifact_id,),
                    ),
                ),
            ),
        ),
        frozen_by=uuid4(),
    )
    result = service.evaluate(
        dataset_id=dataset.dataset_id,
        index_version_id=index_version_id,
        configuration=RetrievalEvaluationConfiguration(k_values=(3,)),
    )
    assert result.passed
    service.activate(index_version_id)
