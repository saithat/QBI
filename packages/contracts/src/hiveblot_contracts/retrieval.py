"""Versioned evidence indexing, retrieval, citation, and evaluation contracts."""

from __future__ import annotations

from enum import StrEnum
from typing import Self
from uuid import UUID

from pydantic import AwareDatetime, Field, StrictBool, StrictFloat, StrictInt, model_validator

from .annotations import ReviewStatus
from .artifacts import ArtifactReference, ArtifactVisibility, BoundingRegion
from .base import ContractModel, Identifier, Sha256Digest
from .identifiers import ModelIdentifier, ToolIdentifier
from .observation import ObservationState


def _require_unique(values: tuple[str, ...], label: str) -> None:
    normalized = tuple(value.strip().casefold() for value in values)
    if any(not value for value in normalized):
        raise ValueError(f"{label} cannot contain blank values")
    if len(normalized) != len(set(normalized)):
        raise ValueError(f"{label} must be unique ignoring case")


class EvidenceQuality(StrEnum):
    RAW_SOURCE = "raw_source"
    SUPPLEMENTARY_SOURCE = "supplementary_source"
    PUBLICATION_FIGURE = "publication_figure"
    EXPLORATORY = "exploratory"
    NOT_ASSESSED = "not_assessed"


class EvidenceRelation(StrEnum):
    SUPPORTS = "supports"
    CONTRADICTS = "contradicts"
    CONTEXT = "context"


class RetrievalMode(StrEnum):
    LEXICAL = "lexical"
    SEMANTIC = "semantic"
    HYBRID = "hybrid"


class SearchIndexStatus(StrEnum):
    BUILDING = "building"
    READY = "ready"
    ACTIVE = "active"
    FAILED = "failed"
    RETIRED = "retired"


class EvidenceCitation(ContractModel):
    artifact: ArtifactReference
    source_uri: str | None = Field(default=None, min_length=1, max_length=4096)
    label: str | None = Field(default=None, min_length=1, max_length=1000)
    page_number: StrictInt | None = Field(default=None, ge=1)
    region: BoundingRegion | None = None

    @model_validator(mode="after")
    def region_matches_artifact_and_page(self) -> Self:
        if self.region is not None and self.region.source_artifact_id != self.artifact.artifact_id:
            raise ValueError("citation geometry must reference the cited artifact")
        if (
            self.region is not None
            and self.page_number is not None
            and self.region.page_number != self.page_number
        ):
            raise ValueError("citation page and geometry page must match")
        return self


class IndexedEvidenceObservation(ContractModel):
    observation_id: UUID
    statement: str = Field(min_length=1, max_length=20_000)
    relation: EvidenceRelation
    state: ObservationState
    protein: str | None = Field(default=None, min_length=1, max_length=1000)
    biological_system: str | None = Field(default=None, min_length=1, max_length=2000)
    treatment: str | None = Field(default=None, min_length=1, max_length=2000)
    condition: str | None = Field(default=None, min_length=1, max_length=4000)
    source_entity_id: UUID | None = None
    citation_artifact_ids: tuple[UUID, ...] = Field(min_length=1)

    @model_validator(mode="after")
    def citations_are_unique(self) -> Self:
        if len(self.citation_artifact_ids) != len(set(self.citation_artifact_ids)):
            raise ValueError("observation citation artifact IDs must be unique")
        return self


class IndexedEvidenceClaim(ContractModel):
    claim_id: UUID
    statement: str = Field(min_length=1, max_length=20_000)
    citation_artifact_ids: tuple[UUID, ...] = Field(min_length=1)

    @model_validator(mode="after")
    def citations_are_unique(self) -> Self:
        if len(self.citation_artifact_ids) != len(set(self.citation_artifact_ids)):
            raise ValueError("claim citation artifact IDs must be unique")
        return self


class EvidenceIndexDocument(ContractModel):
    document_id: UUID
    case_id: UUID
    paper_id: Identifier
    paper_title: str | None = Field(default=None, min_length=1, max_length=4000)
    figure_label: str | None = Field(default=None, min_length=1, max_length=1000)
    experiment_label: str | None = Field(default=None, min_length=1, max_length=2000)
    proteins: tuple[str, ...] = ()
    biological_systems: tuple[str, ...] = ()
    treatments: tuple[str, ...] = ()
    conditions: tuple[str, ...] = ()
    observations: tuple[IndexedEvidenceObservation, ...] = ()
    claims: tuple[IndexedEvidenceClaim, ...] = ()
    review_status: ReviewStatus
    evidence_quality: EvidenceQuality
    citations: tuple[EvidenceCitation, ...] = Field(min_length=1)
    visibility: ArtifactVisibility
    organization_id: UUID | None = None
    source_annotation_revision_id: UUID | None = None
    source_pipeline_publication_id: UUID | None = None
    created_at: AwareDatetime

    @model_validator(mode="after")
    def graph_and_scope_are_consistent(self) -> Self:
        if self.visibility is ArtifactVisibility.PUBLIC and self.organization_id is not None:
            raise ValueError("public index documents cannot belong to an organization")
        if (
            self.visibility is ArtifactVisibility.ORGANIZATION_PRIVATE
            and self.organization_id is None
        ):
            raise ValueError("organization-private index documents require an organization")
        if (
            self.source_annotation_revision_id is None
            and self.source_pipeline_publication_id is None
        ):
            raise ValueError("index documents require a producing annotation or publication")
        if not self.observations and not self.claims:
            raise ValueError("index documents require an observation or explicit claim")
        _require_unique(self.proteins, "protein terms")
        _require_unique(self.biological_systems, "biological-system terms")
        _require_unique(self.treatments, "treatment terms")
        _require_unique(self.conditions, "condition terms")
        observation_ids = tuple(item.observation_id for item in self.observations)
        claim_ids = tuple(item.claim_id for item in self.claims)
        if len(observation_ids) != len(set(observation_ids)):
            raise ValueError("indexed observation IDs must be unique")
        if len(claim_ids) != len(set(claim_ids)):
            raise ValueError("indexed claim IDs must be unique")
        citation_ids = tuple(item.artifact.artifact_id for item in self.citations)
        if len(citation_ids) != len(set(citation_ids)):
            raise ValueError("document citation artifacts must be unique")
        cited = set(citation_ids)
        referenced_observations = {
            artifact_id for item in self.observations for artifact_id in item.citation_artifact_ids
        }
        referenced_claims = {
            artifact_id for item in self.claims for artifact_id in item.citation_artifact_ids
        }
        referenced = referenced_observations | referenced_claims
        if not referenced.issubset(cited):
            raise ValueError("observations and claims must reference document citations")
        return self


class SearchIndexConfigurationRecord(ContractModel):
    configuration_id: UUID
    index_name: Identifier
    configuration_version: Identifier
    embedding_model: ModelIdentifier
    embedding_dimensions: StrictInt = Field(ge=8, le=4096)
    lexical_weight: StrictFloat = Field(ge=0, le=1, allow_inf_nan=False)
    semantic_weight: StrictFloat = Field(ge=0, le=1, allow_inf_nan=False)
    candidate_limit: StrictInt = Field(ge=1, le=10_000)
    reranker: ToolIdentifier | None = None
    reranker_weight: StrictFloat = Field(default=0.0, ge=0, le=0.5, allow_inf_nan=False)
    configuration_sha256: Sha256Digest
    created_by: UUID
    created_at: AwareDatetime

    @model_validator(mode="after")
    def at_least_one_retrieval_channel_is_enabled(self) -> Self:
        if self.lexical_weight + self.semantic_weight <= 0:
            raise ValueError("index configuration requires lexical or semantic retrieval")
        if self.reranker is None and self.reranker_weight != 0:
            raise ValueError("reranker weight requires a versioned reranker")
        if self.reranker is not None and self.reranker_weight <= 0:
            raise ValueError("configured rerankers require a positive weight")
        return self


class SearchIndexVersionRecord(ContractModel):
    index_version_id: UUID
    configuration_id: UUID
    index_name: Identifier
    index_version: Identifier
    status: SearchIndexStatus
    document_count: StrictInt = Field(ge=0)
    manifest_sha256: Sha256Digest | None = None
    failure_reason: str | None = Field(default=None, min_length=1, max_length=4000)
    created_by: UUID
    created_at: AwareDatetime
    built_at: AwareDatetime | None = None
    evaluated_at: AwareDatetime | None = None
    activated_at: AwareDatetime | None = None
    activation_evaluation_run_id: UUID | None = None

    @model_validator(mode="after")
    def lifecycle_timestamps_are_consistent(self) -> Self:
        if self.status is SearchIndexStatus.BUILDING:
            if self.built_at is not None or self.manifest_sha256 is not None:
                raise ValueError("building index versions cannot have a completed manifest")
        elif self.status is SearchIndexStatus.FAILED:
            if self.failure_reason is None:
                raise ValueError("failed index versions require a reason")
        else:
            if self.built_at is None or self.manifest_sha256 is None:
                raise ValueError("completed index versions require build metadata")
        activated_status = self.status in {SearchIndexStatus.ACTIVE, SearchIndexStatus.RETIRED}
        if activated_status and (
            self.activated_at is None or self.activation_evaluation_run_id is None
        ):
            raise ValueError(
                "active and retired index versions require activation evaluation provenance"
            )
        if not activated_status and (
            self.activated_at is not None or self.activation_evaluation_run_id is not None
        ):
            raise ValueError(
                "only active or retired index versions may carry activation provenance"
            )
        if self.activated_at is not None and self.evaluated_at is None:
            raise ValueError("activated index versions must have been evaluated")
        for value in (self.built_at, self.evaluated_at, self.activated_at):
            if value is not None and value < self.created_at:
                raise ValueError("index lifecycle timestamps cannot precede creation")
        return self


class EvidenceSearchFilters(ContractModel):
    proteins: tuple[str, ...] = ()
    biological_systems: tuple[str, ...] = ()
    treatments: tuple[str, ...] = ()
    conditions: tuple[str, ...] = ()
    review_statuses: tuple[ReviewStatus, ...] = ()
    evidence_qualities: tuple[EvidenceQuality, ...] = ()

    @model_validator(mode="after")
    def filters_are_unique(self) -> Self:
        _require_unique(self.proteins, "protein filters")
        _require_unique(self.biological_systems, "biological-system filters")
        _require_unique(self.treatments, "treatment filters")
        _require_unique(self.conditions, "condition filters")
        if len(self.review_statuses) != len(set(self.review_statuses)):
            raise ValueError("review-status filters must be unique")
        if len(self.evidence_qualities) != len(set(self.evidence_qualities)):
            raise ValueError("evidence-quality filters must be unique")
        return self


class EvidenceSearchQuery(ContractModel):
    query: str = Field(min_length=1, max_length=4000)
    filters: EvidenceSearchFilters = EvidenceSearchFilters()
    retrieval_mode: RetrievalMode = RetrievalMode.HYBRID
    rerank: StrictBool = True
    limit: StrictInt = Field(default=20, ge=1, le=100)
    index_version_id: UUID | None = None
    trace_id: UUID


class EvidenceSearchHit(ContractModel):
    rank: StrictInt = Field(ge=1)
    score: StrictFloat = Field(ge=0, le=1, allow_inf_nan=False)
    lexical_score: StrictFloat = Field(ge=0, le=1, allow_inf_nan=False)
    semantic_score: StrictFloat = Field(ge=0, le=1, allow_inf_nan=False)
    reranker_score: StrictFloat | None = Field(default=None, ge=0, le=1, allow_inf_nan=False)
    document: EvidenceIndexDocument
    supporting_observations: tuple[IndexedEvidenceObservation, ...] = ()
    contradictory_observations: tuple[IndexedEvidenceObservation, ...] = ()

    @model_validator(mode="after")
    def observations_are_document_subsets(self) -> Self:
        document_ids = {item.observation_id for item in self.document.observations}
        supporting = {item.observation_id for item in self.supporting_observations}
        contradictory = {item.observation_id for item in self.contradictory_observations}
        if not supporting.issubset(document_ids) or not contradictory.issubset(document_ids):
            raise ValueError("search-hit observations must come from the indexed document")
        if supporting & contradictory:
            raise ValueError("one observation cannot both support and contradict")
        return self


class EvidenceSearchResult(ContractModel):
    query: EvidenceSearchQuery
    index_version: SearchIndexVersionRecord
    hits: tuple[EvidenceSearchHit, ...]
    total_candidates: StrictInt = Field(ge=0)
    elapsed_ms: StrictInt = Field(ge=0)

    @model_validator(mode="after")
    def ranks_are_contiguous(self) -> Self:
        if tuple(item.rank for item in self.hits) != tuple(range(1, len(self.hits) + 1)):
            raise ValueError("search-hit ranks must be contiguous")
        return self


class RetrievalRelevanceJudgment(ContractModel):
    document_id: UUID
    relevance: StrictInt = Field(ge=1, le=3)
    required_citation_artifact_ids: tuple[UUID, ...] = ()

    @model_validator(mode="after")
    def citation_expectations_are_unique(self) -> Self:
        if len(self.required_citation_artifact_ids) != len(
            set(self.required_citation_artifact_ids)
        ):
            raise ValueError("required citation artifact IDs must be unique")
        return self


class RetrievalEvaluationQuery(ContractModel):
    query_id: Identifier
    query: str = Field(min_length=1, max_length=4000)
    relevant: tuple[RetrievalRelevanceJudgment, ...] = Field(min_length=1)
    hard_negative_document_ids: tuple[UUID, ...] = ()
    expected_filters: EvidenceSearchFilters = EvidenceSearchFilters()
    allowed_organization_ids: tuple[UUID, ...] = ()

    @model_validator(mode="after")
    def judgments_are_disjoint_and_unique(self) -> Self:
        relevant_ids = tuple(item.document_id for item in self.relevant)
        if len(relevant_ids) != len(set(relevant_ids)):
            raise ValueError("retrieval relevance judgments must be unique")
        if len(self.hard_negative_document_ids) != len(set(self.hard_negative_document_ids)):
            raise ValueError("hard-negative document IDs must be unique")
        if set(relevant_ids) & set(self.hard_negative_document_ids):
            raise ValueError("relevant and hard-negative document IDs must be disjoint")
        if len(self.allowed_organization_ids) != len(set(self.allowed_organization_ids)):
            raise ValueError("allowed evaluation organizations must be unique")
        return self


class RetrievalEvaluationDatasetRecord(ContractModel):
    dataset_id: UUID
    dataset_name: Identifier
    dataset_version: Identifier
    visibility: ArtifactVisibility
    organization_id: UUID | None = None
    queries: tuple[RetrievalEvaluationQuery, ...] = Field(min_length=1)
    content_sha256: Sha256Digest
    frozen_by: UUID
    frozen_at: AwareDatetime

    @model_validator(mode="after")
    def dataset_scope_and_queries_are_consistent(self) -> Self:
        if self.visibility is ArtifactVisibility.PUBLIC and self.organization_id is not None:
            raise ValueError("public retrieval datasets cannot belong to an organization")
        if (
            self.visibility is ArtifactVisibility.ORGANIZATION_PRIVATE
            and self.organization_id is None
        ):
            raise ValueError("private retrieval datasets require an organization")
        query_ids = tuple(item.query_id for item in self.queries)
        if len(query_ids) != len(set(query_ids)):
            raise ValueError("retrieval evaluation query IDs must be unique")
        return self


class RetrievalEvaluationConfiguration(ContractModel):
    k_values: tuple[StrictInt, ...] = (1, 5, 10)
    minimum_recall_at_largest_k: StrictFloat = Field(default=0.8, ge=0, le=1, allow_inf_nan=False)
    minimum_filter_correctness: StrictFloat = Field(default=1.0, ge=0, le=1, allow_inf_nan=False)
    minimum_citation_correctness: StrictFloat = Field(default=1.0, ge=0, le=1, allow_inf_nan=False)
    maximum_cross_tenant_leakage_rate: StrictFloat = Field(
        default=0.0, ge=0, le=1, allow_inf_nan=False
    )
    retrieval_mode: RetrievalMode = RetrievalMode.HYBRID
    rerank: StrictBool = True

    @model_validator(mode="after")
    def k_values_are_positive_sorted_and_unique(self) -> Self:
        if not self.k_values or any(value < 1 or value > 100 for value in self.k_values):
            raise ValueError("retrieval evaluation k values must be between 1 and 100")
        if tuple(sorted(set(self.k_values))) != self.k_values:
            raise ValueError("retrieval evaluation k values must be sorted and unique")
        return self


class RetrievalMetricAtK(ContractModel):
    k: StrictInt = Field(ge=1)
    value: StrictFloat = Field(ge=0, le=1, allow_inf_nan=False)


class RetrievalQueryMetric(ContractModel):
    query_id: Identifier
    retrieved_document_ids: tuple[UUID, ...]
    recall_at_k: tuple[RetrievalMetricAtK, ...]
    ndcg_at_k: tuple[RetrievalMetricAtK, ...]
    reciprocal_rank: StrictFloat = Field(ge=0, le=1, allow_inf_nan=False)
    filter_correctness: StrictFloat = Field(ge=0, le=1, allow_inf_nan=False)
    citation_correctness: StrictFloat = Field(ge=0, le=1, allow_inf_nan=False)
    cross_tenant_leaks: StrictInt = Field(ge=0)


class RetrievalAggregateMetrics(ContractModel):
    recall_at_k: tuple[RetrievalMetricAtK, ...]
    mean_reciprocal_rank: StrictFloat = Field(ge=0, le=1, allow_inf_nan=False)
    ndcg_at_k: tuple[RetrievalMetricAtK, ...]
    filter_correctness: StrictFloat = Field(ge=0, le=1, allow_inf_nan=False)
    citation_correctness: StrictFloat = Field(ge=0, le=1, allow_inf_nan=False)
    cross_tenant_leakage_rate: StrictFloat = Field(ge=0, le=1, allow_inf_nan=False)


class RetrievalEvaluationRunRecord(ContractModel):
    evaluation_run_id: UUID
    dataset_id: UUID
    dataset_sha256: Sha256Digest
    index_version_id: UUID
    scorer: ToolIdentifier
    configuration: RetrievalEvaluationConfiguration
    metrics: RetrievalAggregateMetrics
    query_metrics: tuple[RetrievalQueryMetric, ...]
    passed: StrictBool
    created_at: AwareDatetime
