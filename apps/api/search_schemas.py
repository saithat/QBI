"""HTTP-only schemas for evidence indexing, retrieval, and retrieval evaluation."""

from __future__ import annotations

from datetime import datetime
from typing import Literal, Self
from uuid import UUID

from hiveblot_contracts import ContractModel
from pydantic import Field, model_validator

from .evaluation_schemas import (
    BoundingRegionInput,
    BoundingRegionResponse,
    JsonTuple,
    JsonUUID,
)
from .metrics_schemas import JsonDatetime

ReviewStatusValue = Literal[
    "unreviewed", "in_review", "reviewed", "needs_adjudication", "adjudicated"
]
EvidenceQualityValue = Literal[
    "raw_source",
    "supplementary_source",
    "publication_figure",
    "exploratory",
    "not_assessed",
]
VisibilityValue = Literal["public", "organization_private"]
RetrievalModeValue = Literal["lexical", "semantic", "hybrid"]


class SearchModelInput(ContractModel):
    provider: str = Field(min_length=1, max_length=200)
    name: str = Field(min_length=1, max_length=200)
    version: str = Field(min_length=1, max_length=200)


class SearchToolInput(ContractModel):
    name: str = Field(min_length=1, max_length=200)
    version: str = Field(min_length=1, max_length=200)


class SearchModelResponse(ContractModel):
    provider: str
    name: str
    version: str


class SearchToolResponse(ContractModel):
    name: str
    version: str


class CreateSearchIndexConfigurationRequest(ContractModel):
    index_name: str = Field(min_length=1, max_length=200)
    configuration_version: str = Field(min_length=1, max_length=200)
    embedding_model: SearchModelInput
    embedding_dimensions: int = Field(ge=8, le=4096)
    lexical_weight: float = Field(ge=0, le=1, allow_inf_nan=False)
    semantic_weight: float = Field(ge=0, le=1, allow_inf_nan=False)
    candidate_limit: int = Field(default=500, ge=1, le=10_000)
    reranker: SearchToolInput | None = None
    reranker_weight: float = Field(default=0, ge=0, le=0.5, allow_inf_nan=False)

    @model_validator(mode="after")
    def configuration_is_consistent(self) -> Self:
        if self.lexical_weight + self.semantic_weight <= 0:
            raise ValueError("lexical or semantic retrieval must be enabled")
        if self.reranker is None and self.reranker_weight != 0:
            raise ValueError("reranker weight requires a reranker")
        if self.reranker is not None and self.reranker_weight <= 0:
            raise ValueError("reranker requires a positive weight")
        return self


class SearchIndexConfigurationResponse(ContractModel):
    configuration_id: UUID
    index_name: str
    configuration_version: str
    embedding_model: SearchModelResponse
    embedding_dimensions: int
    lexical_weight: float
    semantic_weight: float
    candidate_limit: int
    reranker: SearchToolResponse | None
    reranker_weight: float
    configuration_sha256: str
    created_by: UUID
    created_at: datetime


class SearchIndexConfigurationListResponse(ContractModel):
    configurations: tuple[SearchIndexConfigurationResponse, ...]


class ArtifactReferenceInput(ContractModel):
    artifact_id: JsonUUID
    sha256: str = Field(pattern=r"^[a-f0-9]{64}$")
    media_type: str = Field(pattern=r"^[a-z0-9][a-z0-9!#$&^_.+-]*/[a-z0-9][a-z0-9!#$&^_.+-]*$")
    byte_size: int = Field(ge=0)


class EvidenceCitationInput(ContractModel):
    artifact: ArtifactReferenceInput
    source_uri: str | None = Field(default=None, min_length=1, max_length=4096)
    label: str | None = Field(default=None, min_length=1, max_length=1000)
    page_number: int | None = Field(default=None, ge=1)
    region: BoundingRegionInput | None = None


class IndexedObservationInput(ContractModel):
    observation_id: JsonUUID
    statement: str = Field(min_length=1, max_length=20_000)
    relation: Literal["supports", "contradicts", "context"]
    state: Literal["present", "absent", "unknown", "ambiguous", "not_applicable"]
    protein: str | None = Field(default=None, min_length=1, max_length=1000)
    biological_system: str | None = Field(default=None, min_length=1, max_length=2000)
    treatment: str | None = Field(default=None, min_length=1, max_length=2000)
    condition: str | None = Field(default=None, min_length=1, max_length=4000)
    source_entity_id: JsonUUID | None = None
    citation_artifact_ids: JsonTuple[JsonUUID] = Field(min_length=1)


class IndexedClaimInput(ContractModel):
    claim_id: JsonUUID
    statement: str = Field(min_length=1, max_length=20_000)
    citation_artifact_ids: JsonTuple[JsonUUID] = Field(min_length=1)


class EvidenceIndexDocumentInput(ContractModel):
    document_id: JsonUUID
    case_id: JsonUUID
    paper_id: str = Field(min_length=1, max_length=200)
    paper_title: str | None = Field(default=None, min_length=1, max_length=4000)
    figure_label: str | None = Field(default=None, min_length=1, max_length=1000)
    experiment_label: str | None = Field(default=None, min_length=1, max_length=2000)
    proteins: JsonTuple[str] = ()
    biological_systems: JsonTuple[str] = ()
    treatments: JsonTuple[str] = ()
    conditions: JsonTuple[str] = ()
    observations: JsonTuple[IndexedObservationInput] = ()
    claims: JsonTuple[IndexedClaimInput] = ()
    review_status: ReviewStatusValue
    evidence_quality: EvidenceQualityValue
    citations: JsonTuple[EvidenceCitationInput] = Field(min_length=1)
    visibility: VisibilityValue
    organization_id: JsonUUID | None = None
    source_annotation_revision_id: JsonUUID | None = None
    source_pipeline_publication_id: JsonUUID | None = None
    created_at: JsonDatetime


class BuildEvidenceIndexRequest(ContractModel):
    configuration_id: JsonUUID
    index_version: str = Field(min_length=1, max_length=200)
    documents: JsonTuple[EvidenceIndexDocumentInput] = Field(min_length=1, max_length=10000)


class SearchIndexVersionResponse(ContractModel):
    index_version_id: UUID
    configuration_id: UUID
    index_name: str
    index_version: str
    status: Literal["building", "ready", "active", "failed", "retired"]
    document_count: int
    manifest_sha256: str | None
    failure_reason: str | None
    created_by: UUID
    created_at: datetime
    built_at: datetime | None
    evaluated_at: datetime | None
    activated_at: datetime | None
    activation_evaluation_run_id: UUID | None


class SearchIndexVersionListResponse(ContractModel):
    versions: tuple[SearchIndexVersionResponse, ...]


class EvidenceSearchFiltersInput(ContractModel):
    proteins: JsonTuple[str] = ()
    biological_systems: JsonTuple[str] = ()
    treatments: JsonTuple[str] = ()
    conditions: JsonTuple[str] = ()
    review_statuses: JsonTuple[ReviewStatusValue] = ()
    evidence_qualities: JsonTuple[EvidenceQualityValue] = ()


class EvidenceSearchFiltersResponse(ContractModel):
    proteins: tuple[str, ...]
    biological_systems: tuple[str, ...]
    treatments: tuple[str, ...]
    conditions: tuple[str, ...]
    review_statuses: tuple[ReviewStatusValue, ...]
    evidence_qualities: tuple[EvidenceQualityValue, ...]


class EvidenceSearchRequest(ContractModel):
    query: str = Field(min_length=1, max_length=4000)
    filters: EvidenceSearchFiltersInput = EvidenceSearchFiltersInput()
    retrieval_mode: RetrievalModeValue = "hybrid"
    rerank: bool = True
    limit: int = Field(default=20, ge=1, le=100)
    index_version_id: JsonUUID | None = None


class ArtifactReferenceResponse(ContractModel):
    artifact_id: UUID
    sha256: str
    media_type: str
    byte_size: int


class EvidenceCitationResponse(ContractModel):
    artifact: ArtifactReferenceResponse
    artifact_url: str
    source_uri: str | None
    label: str | None
    page_number: int | None
    region: BoundingRegionResponse | None


class IndexedObservationResponse(ContractModel):
    observation_id: UUID
    statement: str
    relation: Literal["supports", "contradicts", "context"]
    state: Literal["present", "absent", "unknown", "ambiguous", "not_applicable"]
    protein: str | None
    biological_system: str | None
    treatment: str | None
    condition: str | None
    source_entity_id: UUID | None
    citation_artifact_ids: tuple[UUID, ...]


class IndexedClaimResponse(ContractModel):
    claim_id: UUID
    statement: str
    citation_artifact_ids: tuple[UUID, ...]


class EvidenceIndexDocumentResponse(ContractModel):
    document_id: UUID
    case_id: UUID
    paper_id: str
    paper_title: str | None
    figure_label: str | None
    experiment_label: str | None
    proteins: tuple[str, ...]
    biological_systems: tuple[str, ...]
    treatments: tuple[str, ...]
    conditions: tuple[str, ...]
    observations: tuple[IndexedObservationResponse, ...]
    claims: tuple[IndexedClaimResponse, ...]
    review_status: ReviewStatusValue
    evidence_quality: EvidenceQualityValue
    citations: tuple[EvidenceCitationResponse, ...]
    visibility: VisibilityValue
    organization_id: UUID | None
    source_annotation_revision_id: UUID | None
    source_pipeline_publication_id: UUID | None
    created_at: datetime


class EvidenceSearchHitResponse(ContractModel):
    rank: int
    score: float
    lexical_score: float
    semantic_score: float
    reranker_score: float | None
    document: EvidenceIndexDocumentResponse
    supporting_observations: tuple[IndexedObservationResponse, ...]
    contradictory_observations: tuple[IndexedObservationResponse, ...]


class EvidenceSearchResponse(ContractModel):
    query: str
    trace_id: UUID
    index_version: SearchIndexVersionResponse
    total_candidates: int
    elapsed_ms: int
    hits: tuple[EvidenceSearchHitResponse, ...]


class RetrievalJudgmentInput(ContractModel):
    document_id: JsonUUID
    relevance: int = Field(ge=1, le=3)
    required_citation_artifact_ids: JsonTuple[JsonUUID] = ()


class RetrievalEvaluationQueryInput(ContractModel):
    query_id: str = Field(min_length=1, max_length=200)
    query: str = Field(min_length=1, max_length=4000)
    relevant: JsonTuple[RetrievalJudgmentInput] = Field(min_length=1)
    hard_negative_document_ids: JsonTuple[JsonUUID] = ()
    expected_filters: EvidenceSearchFiltersInput = EvidenceSearchFiltersInput()
    allowed_organization_ids: JsonTuple[JsonUUID] = ()


class RetrievalJudgmentResponse(ContractModel):
    document_id: UUID
    relevance: int
    required_citation_artifact_ids: tuple[UUID, ...]


class RetrievalEvaluationQueryResponse(ContractModel):
    query_id: str
    query: str
    relevant: tuple[RetrievalJudgmentResponse, ...]
    hard_negative_document_ids: tuple[UUID, ...]
    expected_filters: EvidenceSearchFiltersResponse
    allowed_organization_ids: tuple[UUID, ...]


class FreezeRetrievalDatasetRequest(ContractModel):
    dataset_name: str = Field(min_length=1, max_length=200)
    dataset_version: str = Field(min_length=1, max_length=200)
    visibility: VisibilityValue = "public"
    organization_id: JsonUUID | None = None
    queries: JsonTuple[RetrievalEvaluationQueryInput] = Field(min_length=1)

    @model_validator(mode="after")
    def scope_is_consistent(self) -> Self:
        if self.visibility == "public" and self.organization_id is not None:
            raise ValueError("public retrieval datasets cannot include organization_id")
        if self.visibility == "organization_private" and self.organization_id is None:
            raise ValueError("private retrieval datasets require organization_id")
        return self


class RetrievalEvaluationDatasetResponse(ContractModel):
    dataset_id: UUID
    dataset_name: str
    dataset_version: str
    visibility: VisibilityValue
    organization_id: UUID | None
    queries: tuple[RetrievalEvaluationQueryResponse, ...]
    content_sha256: str
    frozen_by: UUID
    frozen_at: datetime


class RetrievalEvaluationConfigurationInput(ContractModel):
    k_values: JsonTuple[int] = (1, 5, 10)
    minimum_recall_at_largest_k: float = Field(default=0.8, ge=0, le=1)
    minimum_filter_correctness: float = Field(default=1, ge=0, le=1)
    minimum_citation_correctness: float = Field(default=1, ge=0, le=1)
    maximum_cross_tenant_leakage_rate: float = Field(default=0, ge=0, le=1)
    retrieval_mode: RetrievalModeValue = "hybrid"
    rerank: bool = True


class RetrievalEvaluationConfigurationResponse(ContractModel):
    k_values: tuple[int, ...]
    minimum_recall_at_largest_k: float
    minimum_filter_correctness: float
    minimum_citation_correctness: float
    maximum_cross_tenant_leakage_rate: float
    retrieval_mode: RetrievalModeValue
    rerank: bool


class RunRetrievalEvaluationRequest(ContractModel):
    dataset_id: JsonUUID
    index_version_id: JsonUUID
    configuration: RetrievalEvaluationConfigurationInput = RetrievalEvaluationConfigurationInput()


class RetrievalMetricAtKResponse(ContractModel):
    k: int
    value: float


class RetrievalQueryMetricResponse(ContractModel):
    query_id: str
    retrieved_document_ids: tuple[UUID, ...]
    recall_at_k: tuple[RetrievalMetricAtKResponse, ...]
    ndcg_at_k: tuple[RetrievalMetricAtKResponse, ...]
    reciprocal_rank: float
    filter_correctness: float
    citation_correctness: float
    cross_tenant_leaks: int


class RetrievalAggregateMetricsResponse(ContractModel):
    recall_at_k: tuple[RetrievalMetricAtKResponse, ...]
    mean_reciprocal_rank: float
    ndcg_at_k: tuple[RetrievalMetricAtKResponse, ...]
    filter_correctness: float
    citation_correctness: float
    cross_tenant_leakage_rate: float


class RetrievalEvaluationRunResponse(ContractModel):
    evaluation_run_id: UUID
    dataset_id: UUID
    dataset_sha256: str
    index_version_id: UUID
    scorer: SearchToolResponse
    configuration: RetrievalEvaluationConfigurationResponse
    metrics: RetrievalAggregateMetricsResponse
    query_metrics: tuple[RetrievalQueryMetricResponse, ...]
    passed: bool
    created_at: datetime
