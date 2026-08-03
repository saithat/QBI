from __future__ import annotations

from datetime import UTC, datetime
from uuid import UUID, uuid4

import httpx
import pytest
from hiveblot_auth import AuthorizationService, InMemoryAuthorizationRepository, token_digest
from hiveblot_contracts import (
    PLATFORM_OPERATOR_USER_ID,
    ArtifactAcquisitionMethod,
    ArtifactRecord,
    ArtifactVisibility,
    AuthenticatedPrincipal,
    EvidenceRelation,
    OrganizationMembership,
    OrganizationRole,
    ResourceScope,
)
from hiveblot_search import (
    DEFAULT_EMBEDDING_MODEL,
    DEFAULT_RERANKER,
    DeterministicScientificEmbedder,
    EvidenceSearchService,
    InMemoryEvidenceSearchRepository,
    TokenOverlapReranker,
)
from hiveblot_storage import ArtifactService, SourceAdapterRegistry

from apps.api.artifact_dependencies import get_artifact_service
from apps.api.auth_dependencies import get_authorization_service
from apps.api.main import app
from apps.api.search_dependencies import get_evidence_search_service
from hiveblot.settings import get_settings
from tests.fakes.artifacts import InMemoryArtifactRepository, InMemoryObjectStore
from tests.fakes.search import NOW, evidence_document

PEPPER = "p" * 32


@pytest.mark.asyncio
async def test_index_build_evaluation_activation_and_cited_search_api(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    document = evidence_document(
        statement="Western blot evidence contradicts p53 loss after Nutlin-3.",
        relation=EvidenceRelation.CONTRADICTS,
    )
    authorization_repository = InMemoryAuthorizationRepository()
    authorization_repository.scopes[("evaluation_case", document.case_id)] = ResourceScope(
        visibility=ArtifactVisibility.PUBLIC
    )
    assert document.source_annotation_revision_id is not None
    authorization_repository.scopes[
        ("annotation_revision", document.source_annotation_revision_id)
    ] = ResourceScope(visibility=ArtifactVisibility.PUBLIC)
    citation = document.citations[0]
    authorization_repository.scopes[("artifact", citation.artifact.artifact_id)] = ResourceScope(
        visibility=ArtifactVisibility.PUBLIC
    )
    authorization = AuthorizationService(authorization_repository, token_pepper=PEPPER)
    search_repository = InMemoryEvidenceSearchRepository()
    search_service = _search_service(search_repository)
    artifact_repository = InMemoryArtifactRepository()
    artifact_repository.artifacts[citation.artifact.artifact_id] = ArtifactRecord(
        **citation.artifact.model_dump(mode="python"),
        original_filename="figure-2a.png",
        source_uri=citation.source_uri,
        acquisition_method=ArtifactAcquisitionMethod.SOURCE_ADAPTER,
        visibility=ArtifactVisibility.PUBLIC,
        created_at=NOW,
    )
    artifact_service = ArtifactService(
        repository=artifact_repository,
        object_store=InMemoryObjectStore(),
        source_adapters=SourceAdapterRegistry({}),
        max_bytes=1_000_000,
        upload_url_seconds=60,
        download_url_seconds=60,
        clock=lambda: NOW,
    )

    monkeypatch.setenv("AUTHENTICATION_MODE", "disabled")
    get_settings.cache_clear()
    app.dependency_overrides[get_authorization_service] = lambda: authorization
    app.dependency_overrides[get_evidence_search_service] = lambda: search_service
    app.dependency_overrides[get_artifact_service] = lambda: artifact_service
    transport = httpx.ASGITransport(app=app)
    try:
        async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
            configuration = await client.post(
                "/api/v1/evidence-index-configurations",
                json=_configuration_payload(),
            )
            assert configuration.status_code == 201, configuration.text
            built = await client.post(
                "/api/v1/evidence-index-versions",
                json={
                    "schema_version": "1.0",
                    "configuration_id": configuration.json()["configuration_id"],
                    "index_version": "2026-08-02",
                    "documents": [document.model_dump(mode="json")],
                },
            )
            assert built.status_code == 201, built.text
            assert built.json()["status"] == "ready"
            dataset = await client.post(
                "/api/v1/retrieval-evaluation-datasets",
                json={
                    "schema_version": "1.0",
                    "dataset_name": "retrieval-gold",
                    "dataset_version": "1.0.0",
                    "visibility": "public",
                    "queries": [
                        {
                            "schema_version": "1.0",
                            "query_id": "p53-nutlin",
                            "query": "p53 western blot after Nutlin-3",
                            "relevant": [
                                {
                                    "schema_version": "1.0",
                                    "document_id": str(document.document_id),
                                    "relevance": 3,
                                    "required_citation_artifact_ids": [
                                        str(citation.artifact.artifact_id)
                                    ],
                                }
                            ],
                        }
                    ],
                },
            )
            assert dataset.status_code == 201, dataset.text
            evaluation = await client.post(
                "/api/v1/retrieval-evaluation-runs",
                json={
                    "schema_version": "1.0",
                    "dataset_id": dataset.json()["dataset_id"],
                    "index_version_id": built.json()["index_version_id"],
                    "configuration": {
                        "schema_version": "1.0",
                        "k_values": [1],
                    },
                },
            )
            assert evaluation.status_code == 201, evaluation.text
            assert evaluation.json()["passed"] is True
            activated = await client.post(
                f"/api/v1/evidence-index-versions/{built.json()['index_version_id']}/activation"
            )
            assert activated.status_code == 200, activated.text
            assert (
                activated.json()["activation_evaluation_run_id"]
                == evaluation.json()["evaluation_run_id"]
            )
            response = await client.post(
                "/api/v1/evidence-search",
                json={
                    "schema_version": "1.0",
                    "query": "p53 western blot after Nutlin-3",
                    "filters": {
                        "schema_version": "1.0",
                        "proteins": ["p53"],
                        "biological_systems": ["A549"],
                    },
                },
            )
    finally:
        app.dependency_overrides.pop(get_authorization_service, None)
        app.dependency_overrides.pop(get_evidence_search_service, None)
        app.dependency_overrides.pop(get_artifact_service, None)
        get_settings.cache_clear()

    assert response.status_code == 200, response.text
    payload = response.json()
    assert payload["hits"][0]["document"]["citations"][0]["artifact_url"] == (
        f"/api/v1/artifacts/{citation.artifact.artifact_id}"
    )
    assert payload["hits"][0]["document"]["citations"][0]["source_uri"] == citation.source_uri
    assert len(payload["hits"][0]["contradictory_observations"]) == 1
    assert payload["trace_id"]


@pytest.mark.asyncio
async def test_search_api_applies_same_cross_tenant_policy_as_direct_access(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    organization_a = uuid4()
    organization_b = uuid4()
    public = evidence_document(statement="Public p53 western blot evidence.")
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
    ids_a = {UUID(item["document"]["document_id"]) for item in result_a.json()["hits"]}
    ids_b = {UUID(item["document"]["document_id"]) for item in result_b.json()["hits"]}
    assert ids_a == {public.document_id, private_a.document_id}
    assert ids_b == {public.document_id, private_b.document_id}


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
