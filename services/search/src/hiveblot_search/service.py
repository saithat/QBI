"""Versioned evidence-index lifecycle, permission-safe retrieval, and evaluation."""

from __future__ import annotations

import math
import time
from collections.abc import Callable, Sequence
from datetime import UTC, datetime
from uuid import UUID, uuid4

from hiveblot_contracts import (
    ArtifactVisibility,
    EvidenceIndexDocument,
    EvidenceRelation,
    EvidenceSearchHit,
    EvidenceSearchQuery,
    EvidenceSearchResult,
    ModelIdentifier,
    RetrievalEvaluationConfiguration,
    RetrievalEvaluationDatasetRecord,
    RetrievalEvaluationQuery,
    RetrievalEvaluationRunRecord,
    RetrievalMode,
    SearchIndexConfigurationRecord,
    SearchIndexStatus,
    SearchIndexVersionRecord,
    ToolIdentifier,
)

from .embeddings import EmbeddingProvider, EvidenceReranker, cosine_similarity
from .errors import (
    EvidenceSearchNotFound,
    EvidenceSearchSecurityInvariant,
    InvalidEvidenceSearchState,
)
from .evaluation import score_retrieval_results
from .repository import EvidenceSearchRepository, SearchCandidate
from .text import (
    configuration_sha256,
    document_search_text,
    index_manifest_sha256,
    retrieval_dataset_sha256,
)

DEFAULT_RETRIEVAL_SCORER = ToolIdentifier(
    name="hiveblot-retrieval-evaluator",
    version="1.0.0",
)


class EvidenceSearchService:
    def __init__(
        self,
        repository: EvidenceSearchRepository,
        *,
        embedder: EmbeddingProvider,
        reranker: EvidenceReranker | None,
        default_index_name: str,
        scorer: ToolIdentifier = DEFAULT_RETRIEVAL_SCORER,
        clock: Callable[[], datetime] | None = None,
        monotonic: Callable[[], float] | None = None,
        identity: Callable[[], UUID] | None = None,
    ) -> None:
        self._repository = repository
        self._embedder = embedder
        self._reranker = reranker
        self._default_index_name = default_index_name
        self._scorer = scorer
        self._clock = clock or (lambda: datetime.now(UTC))
        self._monotonic = monotonic or time.monotonic
        self._identity = identity or uuid4

    def create_configuration(
        self,
        *,
        index_name: str,
        configuration_version: str,
        embedding_model: ModelIdentifier,
        embedding_dimensions: int,
        lexical_weight: float,
        semantic_weight: float,
        candidate_limit: int,
        reranker: ToolIdentifier | None,
        reranker_weight: float,
        created_by: UUID,
    ) -> SearchIndexConfigurationRecord:
        now = self._clock()
        draft = SearchIndexConfigurationRecord(
            configuration_id=self._identity(),
            index_name=index_name,
            configuration_version=configuration_version,
            embedding_model=embedding_model,
            embedding_dimensions=embedding_dimensions,
            lexical_weight=lexical_weight,
            semantic_weight=semantic_weight,
            candidate_limit=candidate_limit,
            reranker=reranker,
            reranker_weight=reranker_weight,
            configuration_sha256="0" * 64,
            created_by=created_by,
            created_at=now,
        )
        self._validate_implementations(draft)
        record = draft.model_copy(update={"configuration_sha256": configuration_sha256(draft)})
        return self._repository.create_configuration(record)

    def get_configuration(self, configuration_id: UUID) -> SearchIndexConfigurationRecord:
        record = self._repository.get_configuration(configuration_id)
        if record is None:
            raise EvidenceSearchNotFound(
                f"search index configuration {configuration_id} does not exist"
            )
        return record

    def list_configurations(
        self,
        *,
        index_name: str | None,
        limit: int,
    ) -> Sequence[SearchIndexConfigurationRecord]:
        _require_limit(limit)
        return self._repository.list_configurations(index_name=index_name, limit=limit)

    def build_version(
        self,
        *,
        configuration_id: UUID,
        index_version: str,
        documents: Sequence[EvidenceIndexDocument],
        created_by: UUID,
    ) -> SearchIndexVersionRecord:
        if not documents:
            raise InvalidEvidenceSearchState("an evidence index requires at least one document")
        document_ids = tuple(item.document_id for item in documents)
        if len(document_ids) != len(set(document_ids)):
            raise InvalidEvidenceSearchState("index document IDs must be unique")
        configuration = self.get_configuration(configuration_id)
        self._validate_implementations(configuration)
        started = SearchIndexVersionRecord(
            index_version_id=self._identity(),
            configuration_id=configuration_id,
            index_name=configuration.index_name,
            index_version=index_version,
            status=SearchIndexStatus.BUILDING,
            document_count=0,
            created_by=created_by,
            created_at=self._clock(),
        )
        self._repository.start_version(started)
        try:
            prepared = tuple(
                (
                    document,
                    self._embedder.embed(document_search_text(document)),
                    document_search_text(document),
                )
                for document in documents
            )
            if any(
                len(embedding) != configuration.embedding_dimensions for _, embedding, _ in prepared
            ):
                raise InvalidEvidenceSearchState("embedding provider returned the wrong dimensions")
            return self._repository.complete_version(
                started.index_version_id,
                documents=prepared,
                manifest_sha256=index_manifest_sha256(configuration, documents),
                built_at=self._clock(),
            )
        except Exception as exc:
            reason = f"{type(exc).__name__}: {exc}"[:4000]
            self._repository.fail_version(started.index_version_id, reason=reason)
            raise

    def get_version(self, index_version_id: UUID) -> SearchIndexVersionRecord:
        record = self._repository.get_version(index_version_id)
        if record is None:
            raise EvidenceSearchNotFound(f"search index version {index_version_id} does not exist")
        return record

    def list_versions(
        self,
        *,
        index_name: str | None,
        limit: int,
    ) -> Sequence[SearchIndexVersionRecord]:
        _require_limit(limit)
        return self._repository.list_versions(index_name=index_name, limit=limit)

    def search(
        self,
        query: EvidenceSearchQuery,
        *,
        allowed_organization_ids: tuple[UUID, ...] | None,
        allow_inactive: bool = False,
    ) -> EvidenceSearchResult:
        started = self._monotonic()
        version = self._resolve_version(query.index_version_id)
        if version.status is not SearchIndexStatus.ACTIVE and not allow_inactive:
            raise EvidenceSearchNotFound("no active evidence index is available")
        if version.status not in {SearchIndexStatus.READY, SearchIndexStatus.ACTIVE}:
            raise InvalidEvidenceSearchState("only ready or active index versions are searchable")
        configuration = self.get_configuration(version.configuration_id)
        self._validate_implementations(configuration)
        query_embedding = self._embedder.embed(query.query)
        if len(query_embedding) != configuration.embedding_dimensions:
            raise InvalidEvidenceSearchState("embedding provider returned the wrong dimensions")
        page = self._repository.search_candidates(
            version.index_version_id,
            query=query.query,
            query_embedding=query_embedding,
            retrieval_mode=query.retrieval_mode,
            lexical_weight=configuration.lexical_weight,
            semantic_weight=configuration.semantic_weight,
            filters=query.filters,
            allowed_organization_ids=allowed_organization_ids,
            limit=configuration.candidate_limit,
        )
        for candidate in page.candidates:
            if not _scope_allowed(candidate.document, allowed_organization_ids):
                raise EvidenceSearchSecurityInvariant(
                    "persistence returned an index document outside the authorized scope"
                )
        scored = [
            self._score_candidate(query, configuration, query_embedding, item)
            for item in page.candidates
        ]
        scored.sort(key=lambda item: (item[0], str(item[1].document.document_id)), reverse=True)
        hits = tuple(
            EvidenceSearchHit(
                rank=rank,
                score=score,
                lexical_score=candidate.lexical_score,
                semantic_score=semantic,
                reranker_score=reranker_score,
                document=candidate.document,
                supporting_observations=tuple(
                    item
                    for item in candidate.document.observations
                    if item.relation is EvidenceRelation.SUPPORTS
                ),
                contradictory_observations=tuple(
                    item
                    for item in candidate.document.observations
                    if item.relation is EvidenceRelation.CONTRADICTS
                ),
            )
            for rank, (score, candidate, semantic, reranker_score) in enumerate(
                scored[: query.limit], start=1
            )
        )
        elapsed_ms = max(0, math.ceil((self._monotonic() - started) * 1000))
        return EvidenceSearchResult(
            query=query,
            index_version=version,
            hits=hits,
            total_candidates=page.total,
            elapsed_ms=elapsed_ms,
        )

    def freeze_evaluation_dataset(
        self,
        *,
        dataset_name: str,
        dataset_version: str,
        visibility: ArtifactVisibility,
        organization_id: UUID | None,
        queries: Sequence[RetrievalEvaluationQuery],
        frozen_by: UUID,
    ) -> RetrievalEvaluationDatasetRecord:
        record = RetrievalEvaluationDatasetRecord(
            dataset_id=self._identity(),
            dataset_name=dataset_name,
            dataset_version=dataset_version,
            visibility=visibility,
            organization_id=organization_id,
            queries=tuple(queries),
            content_sha256=retrieval_dataset_sha256(queries),
            frozen_by=frozen_by,
            frozen_at=self._clock(),
        )
        return self._repository.create_evaluation_dataset(record)

    def get_evaluation_dataset(self, dataset_id: UUID) -> RetrievalEvaluationDatasetRecord:
        record = self._repository.get_evaluation_dataset(dataset_id)
        if record is None:
            raise EvidenceSearchNotFound(
                f"retrieval evaluation dataset {dataset_id} does not exist"
            )
        return record

    def evaluate(
        self,
        *,
        dataset_id: UUID,
        index_version_id: UUID,
        configuration: RetrievalEvaluationConfiguration,
    ) -> RetrievalEvaluationRunRecord:
        dataset = self.get_evaluation_dataset(dataset_id)
        version = self.get_version(index_version_id)
        if version.status not in {SearchIndexStatus.READY, SearchIndexStatus.ACTIVE}:
            raise InvalidEvidenceSearchState("retrieval evaluation requires a ready index version")
        results = {
            query.query_id: self.search(
                EvidenceSearchQuery(
                    query=query.query,
                    filters=query.expected_filters,
                    retrieval_mode=configuration.retrieval_mode,
                    rerank=configuration.rerank,
                    limit=configuration.k_values[-1],
                    index_version_id=index_version_id,
                    trace_id=self._identity(),
                ),
                allowed_organization_ids=query.allowed_organization_ids,
                allow_inactive=True,
            )
            for query in dataset.queries
        }
        metrics, query_metrics, passed = score_retrieval_results(
            dataset,
            configuration,
            results,
        )
        now = self._clock()
        record = RetrievalEvaluationRunRecord(
            evaluation_run_id=self._identity(),
            dataset_id=dataset.dataset_id,
            dataset_sha256=dataset.content_sha256,
            index_version_id=index_version_id,
            scorer=self._scorer,
            configuration=configuration,
            metrics=metrics,
            query_metrics=query_metrics,
            passed=passed,
            created_at=now,
        )
        created = self._repository.create_evaluation_run(record)
        if passed and version.evaluated_at is None:
            self._repository.mark_evaluated(index_version_id, evaluated_at=now)
        return created

    def get_evaluation_run(self, evaluation_run_id: UUID) -> RetrievalEvaluationRunRecord:
        record = self._repository.get_evaluation_run(evaluation_run_id)
        if record is None:
            raise EvidenceSearchNotFound(
                f"retrieval evaluation run {evaluation_run_id} does not exist"
            )
        return record

    def activate(self, index_version_id: UUID) -> SearchIndexVersionRecord:
        version = self.get_version(index_version_id)
        if version.status is not SearchIndexStatus.READY:
            raise InvalidEvidenceSearchState("only ready index versions can be activated")
        if not self._repository.has_passing_evaluation(index_version_id):
            raise InvalidEvidenceSearchState("index activation requires a passing evaluation")
        return self._repository.activate_version(index_version_id, activated_at=self._clock())

    def _resolve_version(self, requested: UUID | None) -> SearchIndexVersionRecord:
        if requested is not None:
            return self.get_version(requested)
        version = self._repository.get_active_version(self._default_index_name)
        if version is None:
            raise EvidenceSearchNotFound("no active evidence index is available")
        return version

    def _validate_implementations(self, configuration: SearchIndexConfigurationRecord) -> None:
        if configuration.embedding_model != self._embedder.model:
            raise InvalidEvidenceSearchState("configured embedding model is not installed")
        if configuration.embedding_dimensions != self._embedder.dimensions:
            raise InvalidEvidenceSearchState("configured embedding dimensions are not installed")
        if configuration.reranker is None:
            return
        if self._reranker is None or configuration.reranker != self._reranker.tool:
            raise InvalidEvidenceSearchState("configured reranker is not installed")

    def _score_candidate(
        self,
        query: EvidenceSearchQuery,
        configuration: SearchIndexConfigurationRecord,
        query_embedding: tuple[float, ...],
        candidate: SearchCandidate,
    ) -> tuple[float, SearchCandidate, float, float | None]:
        semantic = cosine_similarity(query_embedding, candidate.embedding)
        if query.retrieval_mode is RetrievalMode.LEXICAL:
            base_score = candidate.lexical_score
        elif query.retrieval_mode is RetrievalMode.SEMANTIC:
            base_score = semantic
        else:
            total_weight = configuration.lexical_weight + configuration.semantic_weight
            base_score = (
                configuration.lexical_weight * candidate.lexical_score
                + configuration.semantic_weight * semantic
            ) / total_weight
        reranker_score: float | None = None
        if query.rerank and configuration.reranker is not None:
            assert self._reranker is not None
            reranker_score = self._reranker.score(
                query.query,
                document_search_text(candidate.document),
            )
            base_score = (
                1 - configuration.reranker_weight
            ) * base_score + configuration.reranker_weight * reranker_score
        return max(0.0, min(1.0, base_score)), candidate, semantic, reranker_score


def _scope_allowed(
    document: EvidenceIndexDocument,
    allowed_organization_ids: tuple[UUID, ...] | None,
) -> bool:
    if allowed_organization_ids is None or document.visibility is ArtifactVisibility.PUBLIC:
        return True
    return document.organization_id in set(allowed_organization_ids)


def _require_limit(limit: int) -> None:
    if not 1 <= limit <= 200:
        raise InvalidEvidenceSearchState("list limit must be between 1 and 200")
