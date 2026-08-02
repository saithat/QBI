"""HTTP-only schemas for evaluation scoring, calibration, and pipeline comparison."""

from __future__ import annotations

from datetime import datetime
from typing import Annotated, Literal
from uuid import UUID

from hiveblot_contracts import ContractModel
from pydantic import AwareDatetime, BeforeValidator, Field

from .evaluation_schemas import BoundingRegionInput, JsonTuple, JsonUUID

ReviewStatusInput = Literal[
    "unreviewed", "in_review", "reviewed", "needs_adjudication", "adjudicated"
]
SpatialTypeInput = Literal[
    "figure", "panel", "blot", "lane", "protein_row", "band", "label", "quantification_plot"
]
GroupDimensionInput = Literal[
    "source_type",
    "journal",
    "repository",
    "image_quality",
    "assay_layout",
    "review_status",
    "model_version",
    "pipeline_version",
]
CalibrationCategoryInput = Literal["field", "geometry", "relationship", "numerical", "ranking"]


def _json_datetime(value: object) -> object:
    if isinstance(value, str):
        return datetime.fromisoformat(value.replace("Z", "+00:00"))
    return value


type JsonDatetime = Annotated[AwareDatetime, BeforeValidator(_json_datetime)]


class EvaluationDimensionsInput(ContractModel):
    source_type: str = Field(min_length=1, max_length=200)
    journal: str | None = Field(default=None, min_length=1, max_length=200)
    repository: str | None = Field(default=None, min_length=1, max_length=200)
    image_quality: str | None = Field(default=None, min_length=1, max_length=200)
    assay_layout: str | None = Field(default=None, min_length=1, max_length=200)
    review_status: ReviewStatusInput


class ReferenceFieldInput(ContractModel):
    field_path: str = Field(pattern=r"^(?:/(?:[^/~]|~[01])+)*$", max_length=1000)
    value_json: str = Field(min_length=1)
    evidence_artifact_ids: JsonTuple[JsonUUID] = ()


class PredictedFieldInput(ReferenceFieldInput):
    confidence: float | None = Field(default=None, ge=0, le=1, allow_inf_nan=False)


class ReferenceRegionInput(ContractModel):
    region_key: str = Field(min_length=1, max_length=200)
    annotation_type: SpatialTypeInput
    region: BoundingRegionInput
    evidence_artifact_ids: JsonTuple[JsonUUID] = ()


class PredictedRegionInput(ReferenceRegionInput):
    confidence: float | None = Field(default=None, ge=0, le=1, allow_inf_nan=False)


class ReferenceRelationshipInput(ContractModel):
    subject_key: str = Field(min_length=1, max_length=200)
    relation_type: str = Field(min_length=1, max_length=200)
    object_key: str = Field(min_length=1, max_length=200)
    evidence_artifact_ids: JsonTuple[JsonUUID] = ()


class PredictedRelationshipInput(ReferenceRelationshipInput):
    confidence: float | None = Field(default=None, ge=0, le=1, allow_inf_nan=False)


class ReferenceNumericalInput(ContractModel):
    field_path: str = Field(pattern=r"^(?:/(?:[^/~]|~[01])+)*$", max_length=1000)
    value: float = Field(allow_inf_nan=False)
    unit: str | None = Field(default=None, min_length=1, max_length=200)
    evidence_artifact_ids: JsonTuple[JsonUUID] = ()


class PredictedNumericalInput(ReferenceNumericalInput):
    confidence: float | None = Field(default=None, ge=0, le=1, allow_inf_nan=False)


class RankingJudgmentInput(ContractModel):
    query_id: str = Field(min_length=1, max_length=200)
    item_id: str = Field(min_length=1, max_length=200)
    relevance: int = Field(ge=0, le=4)


class PredictedRankingInput(ContractModel):
    query_id: str = Field(min_length=1, max_length=200)
    item_id: str = Field(min_length=1, max_length=200)
    rank: int = Field(ge=1)
    score: float = Field(allow_inf_nan=False)
    confidence: float | None = Field(default=None, ge=0, le=1, allow_inf_nan=False)


class ReferenceObservationInput(ContractModel):
    fields: JsonTuple[ReferenceFieldInput] = ()
    regions: JsonTuple[ReferenceRegionInput] = ()
    relationships: JsonTuple[ReferenceRelationshipInput] = ()
    numerical_values: JsonTuple[ReferenceNumericalInput] = ()
    ranking_judgments: JsonTuple[RankingJudgmentInput] = ()


class PredictedObservationInput(ContractModel):
    fields: JsonTuple[PredictedFieldInput] = ()
    regions: JsonTuple[PredictedRegionInput] = ()
    relationships: JsonTuple[PredictedRelationshipInput] = ()
    numerical_values: JsonTuple[PredictedNumericalInput] = ()
    ranked_results: JsonTuple[PredictedRankingInput] = ()


class ReferenceCaseInput(ContractModel):
    case_id: JsonUUID
    dimensions: EvaluationDimensionsInput
    reference: ReferenceObservationInput


class EvaluationDatasetInput(ContractModel):
    snapshot_id: JsonUUID
    dataset_name: str = Field(min_length=1, max_length=200)
    dataset_version: str = Field(min_length=1, max_length=200)
    content_sha256: str = Field(pattern=r"^[a-f0-9]{64}$")
    frozen_at: JsonDatetime
    cases: JsonTuple[ReferenceCaseInput] = Field(min_length=1)


class ModelVersionInput(ContractModel):
    provider: str = Field(min_length=1, max_length=200)
    name: str = Field(min_length=1, max_length=200)
    version: str = Field(min_length=1, max_length=200)


class PipelineEvaluationCaseInput(ContractModel):
    case_id: JsonUUID
    publication_ids: JsonTuple[JsonUUID] = Field(min_length=1)
    prediction: PredictedObservationInput


class PipelineSubmissionInput(ContractModel):
    pipeline_name: str = Field(min_length=1, max_length=200)
    pipeline_version: str = Field(min_length=1, max_length=200)
    model_versions: JsonTuple[ModelVersionInput] = ()
    cases: JsonTuple[PipelineEvaluationCaseInput] = Field(min_length=1)


class MetricConfigurationInput(ContractModel):
    bounding_box_iou_threshold: float = Field(default=0.5, ge=0, le=1, allow_inf_nan=False)
    numerical_absolute_tolerance: float = Field(default=0.01, ge=0, allow_inf_nan=False)
    ranking_k: int = Field(default=10, ge=1, le=1000)
    calibration_bins: int = Field(default=10, ge=2, le=100)
    confidence_thresholds: JsonTuple[float] = (0.0, 0.25, 0.5, 0.75, 0.9)
    normalized_string_casefold: bool = True
    group_by: JsonTuple[GroupDimensionInput] = (
        "source_type",
        "journal",
        "repository",
        "image_quality",
        "assay_layout",
        "review_status",
        "model_version",
        "pipeline_version",
    )


class CreateMetricRunRequest(ContractModel):
    dataset: EvaluationDatasetInput
    submission: PipelineSubmissionInput
    scorer_name: str = Field(min_length=1, max_length=200)
    scorer_version: str = Field(min_length=1, max_length=200)
    configuration: MetricConfigurationInput = MetricConfigurationInput()


class CompareMetricRunsRequest(ContractModel):
    baseline_metric_run_id: JsonUUID
    candidate_metric_run_id: JsonUUID
    minimum_delta: float = Field(default=0.0, ge=0, le=1, allow_inf_nan=False)


class MatchMetricResponse(ContractModel):
    true_positive: int
    false_positive: int
    false_negative: int
    precision: float
    recall: float
    f1: float


class FieldMetricResponse(ContractModel):
    exact: MatchMetricResponse
    normalized: MatchMetricResponse


class GeometryMetricResponse(ContractModel):
    match: MatchMetricResponse
    comparable_regions: int
    mean_iou: float


class CitationMetricResponse(ContractModel):
    correct: int
    predicted: int
    expected: int
    correctness: float
    completeness: float
    f1: float


class ProvenanceMetricResponse(ContractModel):
    predicted_observations: int
    observations_with_evidence: int
    completeness: float


class NumericalMetricResponse(ContractModel):
    comparable_values: int
    mean_absolute_error: float
    root_mean_squared_error: float
    mean_relative_error: float
    within_tolerance: float


class RankingMetricResponse(ContractModel):
    queries: int
    recall_at_k: float
    mean_reciprocal_rank: float
    ndcg_at_k: float


class MetricSummaryResponse(ContractModel):
    fields: FieldMetricResponse
    geometry: GeometryMetricResponse
    relationships: MatchMetricResponse
    citations: CitationMetricResponse
    provenance: ProvenanceMetricResponse
    numerical: NumericalMetricResponse
    ranking: RankingMetricResponse
    composite_score: float


class CalibrationBucketResponse(ContractModel):
    lower_bound: float
    upper_bound: float
    count: int
    average_confidence: float
    accuracy: float


class CoveragePointResponse(ContractModel):
    threshold: float
    retained: int
    coverage: float
    precision: float
    risk: float


class CalibrationResponse(ContractModel):
    observations: int
    brier_score: float
    expected_calibration_error: float
    buckets: tuple[CalibrationBucketResponse, ...]
    coverage_curve: tuple[CoveragePointResponse, ...]


class MetricGroupResponse(ContractModel):
    dimension: GroupDimensionInput
    value: str
    case_count: int
    metrics: MetricSummaryResponse
    calibration: CalibrationResponse


class MetricCaseSummaryResponse(ContractModel):
    case_id: UUID
    dimensions: EvaluationDimensionsInput
    metrics: MetricSummaryResponse
    workbench_url: str


class ConfidenceOutcomeResponse(ContractModel):
    category: CalibrationCategoryInput
    observation_key: str
    confidence: float
    correct: bool


class MetricCaseDetailResponse(MetricCaseSummaryResponse):
    confidence_outcomes: tuple[ConfidenceOutcomeResponse, ...]


class ModelVersionResponse(ContractModel):
    provider: str
    name: str
    version: str


class MetricRunResponse(ContractModel):
    metric_run_id: UUID
    dataset_snapshot_id: UUID
    dataset_name: str
    dataset_version: str
    dataset_sha256: str
    scorer_name: str
    scorer_version: str
    pipeline_name: str
    pipeline_version: str
    model_versions: tuple[ModelVersionResponse, ...]
    input_sha256: str
    configuration: MetricConfigurationInput
    overall: MetricSummaryResponse
    calibration: CalibrationResponse
    groups: tuple[MetricGroupResponse, ...]
    cases: tuple[MetricCaseSummaryResponse, ...]
    created_at: datetime


class MetricRunListResponse(ContractModel):
    runs: tuple[MetricRunResponse, ...]


class CalibrationSliceResponse(ContractModel):
    metric_run_id: UUID
    category: CalibrationCategoryInput | None
    calibration: CalibrationResponse


class CaseComparisonResponse(ContractModel):
    case_id: UUID
    baseline_score: float
    candidate_score: float
    delta: float
    outcome: Literal["regression", "unchanged", "improvement"]
    workbench_url: str


class PipelineComparisonResponse(ContractModel):
    comparison_id: UUID
    dataset_snapshot_id: UUID
    dataset_name: str
    dataset_version: str
    dataset_sha256: str
    baseline_metric_run_id: UUID
    baseline_pipeline_name: str
    baseline_pipeline_version: str
    candidate_metric_run_id: UUID
    candidate_pipeline_name: str
    candidate_pipeline_version: str
    minimum_delta: float
    overall_delta: float
    cases: tuple[CaseComparisonResponse, ...]
    regression_case_urls: tuple[str, ...]
    improvement_case_urls: tuple[str, ...]
    created_at: datetime
