"""Opt-in PostgreSQL acceptance coverage for PRD-018 retrieval integrity."""

from __future__ import annotations

import os
from datetime import timedelta
from uuid import UUID, uuid4

import psycopg
import pytest
from hiveblot_auth import AuthorizationService, PostgresAuthorizationRepository
from hiveblot_auth.bootstrap import bootstrap_identity, issue_platform_operator_token
from hiveblot_contracts import (
    PLATFORM_OPERATOR_USER_ID,
    ArtifactReference,
    ArtifactVisibility,
    EvidenceCitation,
    EvidenceIndexDocument,
    EvidenceRelation,
    EvidenceSearchFilters,
    EvidenceSearchQuery,
    IndexedEvidenceClaim,
    IndexedEvidenceObservation,
    OrganizationRole,
    RetrievalEvaluationConfiguration,
    RetrievalEvaluationQuery,
    RetrievalMode,
    RetrievalRelevanceJudgment,
    SearchIndexStatus,
)
from hiveblot_search import (
    DEFAULT_EMBEDDING_MODEL,
    DEFAULT_RERANKER,
    DeterministicScientificEmbedder,
    EvidenceSearchService,
    InvalidEvidenceSearchState,
    PostgresEvidenceSearchRepository,
    TokenOverlapReranker,
)

from hiveblot import db
from hiveblot.settings import Settings
from tests.fakes.search import NOW, evidence_document

pytestmark = pytest.mark.skipif(
    os.getenv("HIVEBLOT_RUN_LIVE_SEARCH") != "1",
    reason="set HIVEBLOT_RUN_LIVE_SEARCH=1 with an isolated PostgreSQL database",
)

PEPPER = b"p" * 32


def test_live_versioned_search_scope_evaluation_and_database_integrity() -> None:
    settings = Settings(_env_file=None)
    db.initialize(settings.database_url)
    platform_token = "hvb_live_platform_" + "s" * 40
    platform_token_id = issue_platform_operator_token(
        settings.database_url,
        token_label="live-search-platform",
        token=platform_token,
        token_pepper=PEPPER,
        created_at=NOW,
        expires_at=NOW + timedelta(days=1),
    )
    platform_principal = AuthorizationService(
        PostgresAuthorizationRepository(settings.database_url),
        token_pepper=PEPPER.decode(),
        clock=lambda: NOW,
    ).authenticate(platform_token)
    assert platform_principal.platform_operator is True
    assert platform_principal.token_id == platform_token_id
    user_a, organization_a, _ = _identity(settings.database_url, "a")
    user_b, organization_b, _ = _identity(settings.database_url, "b")
    assert organization_a is not None
    assert organization_b is not None
    with (
        pytest.raises(psycopg.errors.RaiseException, match="reserved platform identity"),
        psycopg.connect(settings.database_url) as connection,
    ):
        connection.execute(
            """
            INSERT INTO organization_memberships (
                membership_id, organization_id, user_id, role, active, created_at, updated_at
            ) VALUES (%s, %s, %s, 'organization_administrator', TRUE, %s, %s)
            """,
            (uuid4(), organization_a, PLATFORM_OPERATOR_USER_ID, NOW, NOW),
        )

    public = evidence_document(statement="Public western blot supports p53 induction.")
    private_a = evidence_document(
        statement="Organization A western blot contradicts p53 induction.",
        relation=EvidenceRelation.CONTRADICTS,
        visibility=ArtifactVisibility.ORGANIZATION_PRIVATE,
        organization_id=organization_a,
    )
    private_b = evidence_document(
        statement="Organization B private p53 western blot evidence.",
        visibility=ArtifactVisibility.ORGANIZATION_PRIVATE,
        organization_id=organization_b,
    )
    for document, reviewer_id in (
        (public, user_a),
        (private_a, user_a),
        (private_b, user_b),
    ):
        _insert_document_sources(settings.database_url, document, reviewer_id)

    repository = PostgresEvidenceSearchRepository(settings.database_url)
    embedder = DeterministicScientificEmbedder(dimensions=256)
    service = EvidenceSearchService(
        repository,
        embedder=embedder,
        reranker=TokenOverlapReranker(),
        default_index_name="western-blot-evidence",
        clock=lambda: NOW,
    )
    configuration = service.create_configuration(
        index_name="western-blot-evidence",
        configuration_version="1.0.0",
        embedding_model=DEFAULT_EMBEDDING_MODEL,
        embedding_dimensions=256,
        lexical_weight=0.5,
        semantic_weight=0.5,
        candidate_limit=100,
        reranker=DEFAULT_RERANKER,
        reranker_weight=0.15,
        created_by=PLATFORM_OPERATOR_USER_ID,
    )
    version = service.build_version(
        configuration_id=configuration.configuration_id,
        index_version="live-v1",
        documents=(public, private_a, private_b),
        created_by=PLATFORM_OPERATOR_USER_ID,
    )
    assert version.status is SearchIndexStatus.READY

    preview = service.search(
        EvidenceSearchQuery(
            query="p53 western blot",
            filters=EvidenceSearchFilters(proteins=("p53",)),
            limit=10,
            index_version_id=version.index_version_id,
            trace_id=uuid4(),
        ),
        allowed_organization_ids=(organization_a,),
        allow_inactive=True,
    )
    assert {item.document.document_id for item in preview.hits} == {
        public.document_id,
        private_a.document_id,
    }
    assert private_b.document_id not in {item.document.document_id for item in preview.hits}

    semantic_candidates = repository.search_candidates(
        version.index_version_id,
        query="contradicts",
        query_embedding=embedder.embed("contradicts"),
        retrieval_mode=RetrievalMode.SEMANTIC,
        lexical_weight=0.5,
        semantic_weight=0.5,
        filters=EvidenceSearchFilters(),
        allowed_organization_ids=(organization_a,),
        limit=1,
    )
    assert [item.document.document_id for item in semantic_candidates.candidates] == [
        private_a.document_id
    ]

    with pytest.raises(InvalidEvidenceSearchState, match="passing evaluation"):
        service.activate(version.index_version_id)
    dataset = service.freeze_evaluation_dataset(
        dataset_name="live-retrieval-gold",
        dataset_version="1.0.0",
        visibility=ArtifactVisibility.PUBLIC,
        organization_id=None,
        queries=(
            RetrievalEvaluationQuery(
                query_id="p53",
                query="p53 western blot",
                relevant=(
                    _judgment(public, 3),
                    _judgment(private_a, 2),
                ),
                hard_negative_document_ids=(private_b.document_id,),
                expected_filters=EvidenceSearchFilters(proteins=("p53",)),
                allowed_organization_ids=(organization_a,),
            ),
        ),
        frozen_by=PLATFORM_OPERATOR_USER_ID,
    )
    evaluation = service.evaluate(
        dataset_id=dataset.dataset_id,
        index_version_id=version.index_version_id,
        configuration=RetrievalEvaluationConfiguration(k_values=(1, 3)),
    )
    assert evaluation.passed
    assert evaluation.metrics.cross_tenant_leakage_rate == 0
    activated = service.activate(version.index_version_id)
    assert activated.status is SearchIndexStatus.ACTIVE
    assert activated.activation_evaluation_run_id == evaluation.evaluation_run_id

    with (
        pytest.raises(psycopg.errors.RaiseException, match="immutable"),
        psycopg.connect(settings.database_url) as connection,
    ):
        connection.execute(
            """
            DELETE FROM evidence_index_document_citations
            WHERE index_version_id = %s AND document_id = %s
            """,
            (version.index_version_id, public.document_id),
        )

    invalid = _public_document_citing_private_artifact(private_a)
    _insert_document_sources(
        settings.database_url,
        invalid,
        user_a,
        insert_citation=False,
    )
    with pytest.raises(InvalidEvidenceSearchState, match="public index documents"):
        service.build_version(
            configuration_id=configuration.configuration_id,
            index_version="invalid-citation-scope",
            documents=(invalid,),
            created_by=PLATFORM_OPERATOR_USER_ID,
        )

    citation = public.citations[0]
    forged_metadata = public.model_copy(
        update={
            "document_id": uuid4(),
            "citations": (
                EvidenceCitation(
                    artifact=ArtifactReference(
                        artifact_id=citation.artifact.artifact_id,
                        sha256="f" * 64,
                        media_type=citation.artifact.media_type,
                        byte_size=citation.artifact.byte_size,
                    ),
                    source_uri=citation.source_uri,
                    label=citation.label,
                    page_number=citation.page_number,
                ),
            ),
        }
    )
    with pytest.raises(InvalidEvidenceSearchState, match="citation metadata must match"):
        service.build_version(
            configuration_id=configuration.configuration_id,
            index_version="invalid-citation-metadata",
            documents=(forged_metadata,),
            created_by=PLATFORM_OPERATOR_USER_ID,
        )
    assert any(
        item.status is SearchIndexStatus.FAILED
        for item in service.list_versions(index_name="western-blot-evidence", limit=10)
    )

    with (
        pytest.raises(psycopg.errors.RaiseException, match="append-only"),
        psycopg.connect(settings.database_url) as connection,
    ):
        connection.execute(
            "UPDATE retrieval_evaluation_runs SET passed = FALSE WHERE evaluation_run_id = %s",
            (evaluation.evaluation_run_id,),
        )


def _identity(database_url: str, label: str) -> tuple[UUID, UUID | None, UUID]:
    return bootstrap_identity(
        database_url,
        email=f"search-{label}-{uuid4()}@example.test",
        display_name=f"Search {label}",
        organization_slug=f"search-{label}-{uuid4().hex[:12]}",
        organization_name=f"Search organization {label}",
        role=OrganizationRole.ADMINISTRATOR.value,
        token_label=f"search-{label}",
        token="hvb_live_" + label * 40,
        token_pepper=PEPPER,
        created_at=NOW,
        expires_at=NOW + timedelta(days=1),
    )


def _insert_document_sources(
    database_url: str,
    document: EvidenceIndexDocument,
    reviewer_id: UUID,
    *,
    insert_citation: bool = True,
) -> None:
    citation = document.citations[0]
    with psycopg.connect(database_url) as connection:
        if insert_citation:
            connection.execute(
                """
                INSERT INTO artifact_blobs (sha256, media_type, byte_size, storage_key)
                VALUES (%s, %s, %s, %s)
                ON CONFLICT (sha256) DO NOTHING
                """,
                (
                    citation.artifact.sha256,
                    citation.artifact.media_type,
                    citation.artifact.byte_size,
                    f"search/{citation.artifact.sha256}",
                ),
            )
            connection.execute(
                """
                INSERT INTO artifacts (
                    artifact_id, blob_sha256, original_filename, source_uri,
                    acquisition_method, visibility, organization_id, created_at
                ) VALUES (%s, %s, %s, %s, 'source_adapter', %s, %s, %s)
                ON CONFLICT (artifact_id) DO NOTHING
                """,
                (
                    citation.artifact.artifact_id,
                    citation.artifact.sha256,
                    f"{citation.artifact.artifact_id}.png",
                    citation.source_uri,
                    document.visibility.value,
                    document.organization_id,
                    NOW,
                ),
            )
        connection.execute(
            """
            INSERT INTO evaluation_cases (
                case_id, case_key, review_status, version, created_at, updated_at,
                visibility, organization_id
            ) VALUES (%s, %s, %s, 1, %s, %s, %s, %s)
            """,
            (
                document.case_id,
                f"search:{document.case_id}",
                document.review_status.value,
                NOW,
                NOW,
                document.visibility.value,
                document.organization_id,
            ),
        )
        annotation_id = uuid4()
        assert document.source_annotation_revision_id is not None
        connection.execute(
            """
            INSERT INTO annotation_documents (
                annotation_id, case_id, reviewer_id, head_revision_id, revision_count,
                created_at, updated_at, visibility, organization_id
            ) VALUES (%s, %s, %s, %s, 1, %s, %s, %s, %s)
            """,
            (
                annotation_id,
                document.case_id,
                reviewer_id,
                document.source_annotation_revision_id,
                NOW,
                NOW,
                document.visibility.value,
                document.organization_id,
            ),
        )
        connection.execute(
            """
            INSERT INTO annotation_revisions (
                revision_id, annotation_id, revision_number, reviewer_id, rationale, created_at
            ) VALUES (%s, %s, 1, %s, 'search index source', %s)
            """,
            (document.source_annotation_revision_id, annotation_id, reviewer_id, NOW),
        )


def _judgment(
    document: EvidenceIndexDocument,
    relevance: int,
) -> RetrievalRelevanceJudgment:
    return RetrievalRelevanceJudgment(
        document_id=document.document_id,
        relevance=relevance,
        required_citation_artifact_ids=(document.citations[0].artifact.artifact_id,),
    )


def _public_document_citing_private_artifact(
    private: EvidenceIndexDocument,
) -> EvidenceIndexDocument:
    source = evidence_document(statement="Public document with invalid private citation.")
    private_citation = EvidenceCitation.model_validate(
        private.citations[0].model_dump(mode="python")
    )
    artifact_id = private_citation.artifact.artifact_id
    observations = tuple(
        IndexedEvidenceObservation.model_validate(
            {
                **item.model_dump(mode="python"),
                "citation_artifact_ids": (artifact_id,),
            }
        )
        for item in source.observations
    )
    claims = tuple(
        IndexedEvidenceClaim.model_validate(
            {
                **item.model_dump(mode="python"),
                "citation_artifact_ids": (artifact_id,),
            }
        )
        for item in source.claims
    )
    return EvidenceIndexDocument.model_validate(
        {
            **source.model_dump(mode="python"),
            "observations": observations,
            "claims": claims,
            "citations": (private_citation,),
        }
    )
