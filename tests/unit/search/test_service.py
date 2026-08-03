from __future__ import annotations

from uuid import UUID, uuid4

import pytest
from hiveblot_contracts import (
    ArtifactVisibility,
    EvidenceIndexDocument,
    EvidenceRelation,
    EvidenceSearchFilters,
    EvidenceSearchQuery,
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
    EvidenceSearchNotFound,
    EvidenceSearchService,
    InMemoryEvidenceSearchRepository,
    InvalidEvidenceSearchState,
    TokenOverlapReranker,
)

from tests.fakes.search import NOW, evidence_document


def test_versioned_hybrid_search_filters_tenants_before_scoring_and_requires_evaluation() -> None:
    organization_a = uuid4()
    organization_b = uuid4()
    public = evidence_document(
        statement="Western blot shows Nutlin-3 increases p53 abundance.",
    )
    private_a = evidence_document(
        statement="A repeat western blot contradicts the reported p53 increase.",
        relation=EvidenceRelation.CONTRADICTS,
        visibility=ArtifactVisibility.ORGANIZATION_PRIVATE,
        organization_id=organization_a,
    )
    private_b = evidence_document(
        statement="Private organization B western blot supports p53 induction.",
        visibility=ArtifactVisibility.ORGANIZATION_PRIVATE,
        organization_id=organization_b,
    )
    repository = InMemoryEvidenceSearchRepository()
    service = _service(repository)
    configuration = service.create_configuration(
        index_name="western-blot-evidence",
        configuration_version="1.0.0",
        embedding_model=DEFAULT_EMBEDDING_MODEL,
        embedding_dimensions=256,
        lexical_weight=0.55,
        semantic_weight=0.45,
        candidate_limit=100,
        reranker=DEFAULT_RERANKER,
        reranker_weight=0.15,
        created_by=uuid4(),
    )
    version = service.build_version(
        configuration_id=configuration.configuration_id,
        index_version="2026-08-02",
        documents=(public, private_a, private_b),
        created_by=uuid4(),
    )

    assert version.status is SearchIndexStatus.READY
    with pytest.raises(EvidenceSearchNotFound, match="active"):
        service.search(_query(), allowed_organization_ids=(organization_a,))
    with pytest.raises(InvalidEvidenceSearchState, match="passing evaluation"):
        service.activate(version.index_version_id)

    dataset = service.freeze_evaluation_dataset(
        dataset_name="retrieval-gold",
        dataset_version="1.0.0",
        visibility=ArtifactVisibility.PUBLIC,
        organization_id=None,
        queries=(
            RetrievalEvaluationQuery(
                query_id="p53-nutlin",
                query="p53 western blot after Nutlin-3",
                relevant=(
                    _judgment(public, relevance=3),
                    _judgment(private_a, relevance=2),
                ),
                hard_negative_document_ids=(private_b.document_id,),
                expected_filters=EvidenceSearchFilters(
                    proteins=("p53",),
                    biological_systems=("A549",),
                ),
                allowed_organization_ids=(organization_a,),
            ),
        ),
        frozen_by=uuid4(),
    )
    evaluation = service.evaluate(
        dataset_id=dataset.dataset_id,
        index_version_id=version.index_version_id,
        configuration=RetrievalEvaluationConfiguration(k_values=(1, 3)),
    )

    assert evaluation.passed is True
    assert evaluation.metrics.recall_at_k[-1].value == 1
    assert evaluation.metrics.cross_tenant_leakage_rate == 0
    activated = service.activate(version.index_version_id)
    assert activated.status is SearchIndexStatus.ACTIVE
    assert activated.activation_evaluation_run_id == evaluation.evaluation_run_id

    result_a = service.search(_query(), allowed_organization_ids=(organization_a,))
    ids_a = {item.document.document_id for item in result_a.hits}
    assert ids_a == {public.document_id, private_a.document_id}
    assert private_b.document_id not in ids_a
    assert result_a.hits[0].reranker_score is not None
    assert any(item.contradictory_observations for item in result_a.hits)

    public_only = service.search(_query(), allowed_organization_ids=())
    assert [item.document.document_id for item in public_only.hits] == [public.document_id]


def test_semantic_synonym_search_and_new_activation_retires_prior_version() -> None:
    repository = InMemoryEvidenceSearchRepository()
    service = _service(repository)
    configuration = service.create_configuration(
        index_name="western-blot-evidence",
        configuration_version="1.0.0",
        embedding_model=DEFAULT_EMBEDDING_MODEL,
        embedding_dimensions=256,
        lexical_weight=0.25,
        semantic_weight=0.75,
        candidate_limit=100,
        reranker=None,
        reranker_weight=0,
        created_by=uuid4(),
    )
    document = evidence_document(statement="Western blot confirms p53 induction.")
    first = service.build_version(
        configuration_id=configuration.configuration_id,
        index_version="v1",
        documents=(document,),
        created_by=uuid4(),
    )
    _evaluate_and_activate(service, first.index_version_id, document, "gold-v1")

    result = service.search(
        EvidenceSearchQuery(
            query="p53 immunoblot",
            retrieval_mode=RetrievalMode.SEMANTIC,
            rerank=False,
            limit=10,
            trace_id=uuid4(),
        ),
        allowed_organization_ids=(),
    )
    assert result.hits[0].document.document_id == document.document_id
    assert result.hits[0].semantic_score > 0

    second_document = document.model_copy(update={"document_id": uuid4()})
    second = service.build_version(
        configuration_id=configuration.configuration_id,
        index_version="v2",
        documents=(second_document,),
        created_by=uuid4(),
    )
    _evaluate_and_activate(
        service,
        second.index_version_id,
        second_document,
        "gold-v2",
    )

    assert service.get_version(first.index_version_id).status is SearchIndexStatus.RETIRED
    assert service.get_version(second.index_version_id).status is SearchIndexStatus.ACTIVE


def test_semantic_candidate_selection_occurs_before_candidate_limit() -> None:
    repository = InMemoryEvidenceSearchRepository()
    service = _service(repository)
    configuration = service.create_configuration(
        index_name="semantic-candidate-evidence",
        configuration_version="1.0.0",
        embedding_model=DEFAULT_EMBEDDING_MODEL,
        embedding_dimensions=256,
        lexical_weight=0.25,
        semantic_weight=0.75,
        candidate_limit=1,
        reranker=None,
        reranker_weight=0,
        created_by=uuid4(),
    )
    relevant = evidence_document(
        statement=" ".join(["contradiction"] * 20),
    ).model_copy(update={"document_id": UUID(int=1)})
    lexical_tie_with_higher_id = evidence_document(
        statement="Contradiction plus an unrelated loading-control observation.",
    ).model_copy(update={"document_id": UUID(int=2)})
    version = service.build_version(
        configuration_id=configuration.configuration_id,
        index_version="semantic-candidates-v1",
        documents=(relevant, lexical_tie_with_higher_id),
        created_by=uuid4(),
    )

    result = service.search(
        EvidenceSearchQuery(
            query="contradiction",
            retrieval_mode=RetrievalMode.SEMANTIC,
            rerank=False,
            limit=1,
            index_version_id=version.index_version_id,
            trace_id=uuid4(),
        ),
        allowed_organization_ids=(),
        allow_inactive=True,
    )

    assert [item.document.document_id for item in result.hits] == [relevant.document_id]


def _service(repository: InMemoryEvidenceSearchRepository) -> EvidenceSearchService:
    return EvidenceSearchService(
        repository,
        embedder=DeterministicScientificEmbedder(dimensions=256),
        reranker=TokenOverlapReranker(),
        default_index_name="western-blot-evidence",
        clock=lambda: NOW,
    )


def _query() -> EvidenceSearchQuery:
    return EvidenceSearchQuery(
        query="p53 western blot after Nutlin-3",
        filters=EvidenceSearchFilters(proteins=("p53",), biological_systems=("A549",)),
        limit=10,
        trace_id=uuid4(),
    )


def _judgment(
    document: EvidenceIndexDocument,
    *,
    relevance: int,
) -> RetrievalRelevanceJudgment:
    return RetrievalRelevanceJudgment(
        document_id=document.document_id,
        relevance=relevance,
        required_citation_artifact_ids=(document.citations[0].artifact.artifact_id,),
    )


def _evaluate_and_activate(
    service: EvidenceSearchService,
    index_version_id: UUID,
    document: EvidenceIndexDocument,
    dataset_version: str,
) -> None:
    dataset = service.freeze_evaluation_dataset(
        dataset_name="activation-gold",
        dataset_version=dataset_version,
        visibility=ArtifactVisibility.PUBLIC,
        organization_id=None,
        queries=(
            RetrievalEvaluationQuery(
                query_id=f"query-{dataset_version}",
                query="p53 immunoblot",
                relevant=(_judgment(document, relevance=3),),
            ),
        ),
        frozen_by=uuid4(),
    )
    result = service.evaluate(
        dataset_id=dataset.dataset_id,
        index_version_id=index_version_id,
        configuration=RetrievalEvaluationConfiguration(k_values=(1,)),
    )
    assert result.passed
    activated = service.activate(index_version_id)
    assert activated.activation_evaluation_run_id == result.evaluation_run_id
