"""Frozen evaluation inputs, versioned metric results, calibration, and comparisons."""

from __future__ import annotations

import hashlib
import json
import math
from collections.abc import Hashable, Iterable
from enum import StrEnum
from typing import Self
from uuid import UUID

from pydantic import AwareDatetime, Field, model_validator

from .annotations import ReviewStatus, SpatialAnnotationType
from .artifacts import BoundingRegion
from .base import ContractModel, Identifier, Sha256Digest
from .identifiers import ModelIdentifier, PipelineIdentifier, ToolIdentifier

JsonPointer = str
Confidence = float


class EvaluationCaseDimensions(ContractModel):
    source_type: Identifier
    journal: Identifier | None = None
    repository: Identifier | None = None
    image_quality: Identifier | None = None
    assay_layout: Identifier | None = None
    review_status: ReviewStatus


class _EvidenceObservation(ContractModel):
    evidence_artifact_ids: tuple[UUID, ...] = ()

    @model_validator(mode="after")
    def evidence_is_unique(self) -> Self:
        if len(self.evidence_artifact_ids) != len(set(self.evidence_artifact_ids)):
            raise ValueError("evidence artifact IDs must be unique")
        return self


class ReferenceFieldObservation(_EvidenceObservation):
    field_path: JsonPointer = Field(pattern=r"^(?:/(?:[^/~]|~[01])+)*$", max_length=1000)
    value_json: str = Field(min_length=1)

    @model_validator(mode="after")
    def value_is_json(self) -> Self:
        _parse_json(self.value_json, "field value_json")
        return self


class PredictedFieldObservation(ReferenceFieldObservation):
    confidence: Confidence | None = Field(default=None, ge=0, le=1, allow_inf_nan=False)


class ReferenceRegionObservation(_EvidenceObservation):
    region_key: Identifier
    annotation_type: SpatialAnnotationType
    region: BoundingRegion


class PredictedRegionObservation(ReferenceRegionObservation):
    confidence: Confidence | None = Field(default=None, ge=0, le=1, allow_inf_nan=False)


class ReferenceRelationshipObservation(_EvidenceObservation):
    subject_key: Identifier
    relation_type: Identifier
    object_key: Identifier

    @model_validator(mode="after")
    def relationship_is_not_reflexive(self) -> Self:
        if self.subject_key == self.object_key:
            raise ValueError("evaluation relationships cannot be reflexive")
        return self


class PredictedRelationshipObservation(ReferenceRelationshipObservation):
    confidence: Confidence | None = Field(default=None, ge=0, le=1, allow_inf_nan=False)


class ReferenceNumericalObservation(_EvidenceObservation):
    field_path: JsonPointer = Field(pattern=r"^(?:/(?:[^/~]|~[01])+)*$", max_length=1000)
    value: float = Field(allow_inf_nan=False)
    unit: Identifier | None = None


class PredictedNumericalObservation(ReferenceNumericalObservation):
    confidence: Confidence | None = Field(default=None, ge=0, le=1, allow_inf_nan=False)


class RankingJudgment(ContractModel):
    query_id: Identifier
    item_id: Identifier
    relevance: int = Field(ge=0, le=4)


class PredictedRankingResult(ContractModel):
    query_id: Identifier
    item_id: Identifier
    rank: int = Field(ge=1)
    score: float = Field(allow_inf_nan=False)
    confidence: Confidence | None = Field(default=None, ge=0, le=1, allow_inf_nan=False)


class ReferenceObservationDocument(ContractModel):
    fields: tuple[ReferenceFieldObservation, ...] = ()
    regions: tuple[ReferenceRegionObservation, ...] = ()
    relationships: tuple[ReferenceRelationshipObservation, ...] = ()
    numerical_values: tuple[ReferenceNumericalObservation, ...] = ()
    ranking_judgments: tuple[RankingJudgment, ...] = ()

    @model_validator(mode="after")
    def keys_are_unique(self) -> Self:
        _require_unique((item.field_path for item in self.fields), "reference field paths")
        _require_unique((item.region_key for item in self.regions), "reference region keys")
        _require_unique(
            (
                (item.subject_key, item.relation_type, item.object_key)
                for item in self.relationships
            ),
            "reference relationships",
        )
        _require_unique(
            (item.field_path for item in self.numerical_values),
            "reference numerical field paths",
        )
        _require_unique(
            ((item.query_id, item.item_id) for item in self.ranking_judgments),
            "ranking judgments",
        )
        return self


class PredictedObservationDocument(ContractModel):
    fields: tuple[PredictedFieldObservation, ...] = ()
    regions: tuple[PredictedRegionObservation, ...] = ()
    relationships: tuple[PredictedRelationshipObservation, ...] = ()
    numerical_values: tuple[PredictedNumericalObservation, ...] = ()
    ranked_results: tuple[PredictedRankingResult, ...] = ()

    @model_validator(mode="after")
    def keys_are_unique(self) -> Self:
        _require_unique((item.field_path for item in self.fields), "predicted field paths")
        _require_unique((item.region_key for item in self.regions), "predicted region keys")
        _require_unique(
            (
                (item.subject_key, item.relation_type, item.object_key)
                for item in self.relationships
            ),
            "predicted relationships",
        )
        _require_unique(
            (item.field_path for item in self.numerical_values),
            "predicted numerical field paths",
        )
        _require_unique(
            ((item.query_id, item.item_id) for item in self.ranked_results),
            "ranked results",
        )
        _require_unique(
            ((item.query_id, item.rank) for item in self.ranked_results),
            "ranking positions",
        )
        return self


class EvaluationReferenceCase(ContractModel):
    case_id: UUID
    dimensions: EvaluationCaseDimensions
    reference: ReferenceObservationDocument


class EvaluationDatasetSnapshot(ContractModel):
    snapshot_id: UUID
    dataset_name: Identifier
    dataset_version: Identifier
    content_sha256: Sha256Digest
    frozen_at: AwareDatetime
    cases: tuple[EvaluationReferenceCase, ...] = Field(min_length=1)

    @model_validator(mode="after")
    def snapshot_is_content_addressed(self) -> Self:
        _require_unique((item.case_id for item in self.cases), "evaluation case IDs")
        expected = evaluation_dataset_content_sha256(self.cases)
        if self.content_sha256 != expected:
            raise ValueError("evaluation dataset content_sha256 does not match its cases")
        return self


class PipelineEvaluationCase(ContractModel):
    case_id: UUID
    publication_ids: tuple[UUID, ...] = Field(min_length=1)
    prediction: PredictedObservationDocument

    @model_validator(mode="after")
    def publications_are_unique(self) -> Self:
        _require_unique(self.publication_ids, "pipeline publication IDs")
        return self


class PipelineEvaluationSubmission(ContractModel):
    pipeline: PipelineIdentifier
    model_versions: tuple[ModelIdentifier, ...] = ()
    cases: tuple[PipelineEvaluationCase, ...] = Field(min_length=1)

    @model_validator(mode="after")
    def submission_is_unique(self) -> Self:
        _require_unique((item.case_id for item in self.cases), "submitted evaluation case IDs")
        _require_unique(
            ((item.provider, item.name, item.version) for item in self.model_versions),
            "submitted model versions",
        )
        return self


class EvaluationGroupDimension(StrEnum):
    SOURCE_TYPE = "source_type"
    JOURNAL = "journal"
    REPOSITORY = "repository"
    IMAGE_QUALITY = "image_quality"
    ASSAY_LAYOUT = "assay_layout"
    REVIEW_STATUS = "review_status"
    MODEL_VERSION = "model_version"
    PIPELINE_VERSION = "pipeline_version"


class EvaluationMetricConfiguration(ContractModel):
    bounding_box_iou_threshold: float = Field(default=0.5, ge=0, le=1, allow_inf_nan=False)
    numerical_absolute_tolerance: float = Field(default=0.01, ge=0, allow_inf_nan=False)
    ranking_k: int = Field(default=10, ge=1, le=1000)
    calibration_bins: int = Field(default=10, ge=2, le=100)
    confidence_thresholds: tuple[float, ...] = (0.0, 0.25, 0.5, 0.75, 0.9)
    normalized_string_casefold: bool = True
    group_by: tuple[EvaluationGroupDimension, ...] = tuple(EvaluationGroupDimension)

    @model_validator(mode="after")
    def configuration_is_valid(self) -> Self:
        if len(self.group_by) != len(set(self.group_by)):
            raise ValueError("group_by dimensions must be unique")
        if len(self.confidence_thresholds) != len(set(self.confidence_thresholds)):
            raise ValueError("confidence thresholds must be unique")
        if any(
            value < 0 or value > 1 or not math.isfinite(value)
            for value in self.confidence_thresholds
        ):
            raise ValueError("confidence thresholds must be finite values between zero and one")
        if tuple(sorted(self.confidence_thresholds)) != self.confidence_thresholds:
            raise ValueError("confidence thresholds must be ordered")
        return self


class EvaluationScoringInput(ContractModel):
    dataset: EvaluationDatasetSnapshot
    submission: PipelineEvaluationSubmission
    scorer: ToolIdentifier
    configuration: EvaluationMetricConfiguration = EvaluationMetricConfiguration()

    @model_validator(mode="after")
    def case_sets_match(self) -> Self:
        reference_ids = {item.case_id for item in self.dataset.cases}
        submission_ids = {item.case_id for item in self.submission.cases}
        if reference_ids != submission_ids:
            raise ValueError("pipeline submission cases must exactly match the frozen dataset")
        return self


class MatchMetricSummary(ContractModel):
    true_positive: int = Field(ge=0)
    false_positive: int = Field(ge=0)
    false_negative: int = Field(ge=0)
    precision: float = Field(ge=0, le=1, allow_inf_nan=False)
    recall: float = Field(ge=0, le=1, allow_inf_nan=False)
    f1: float = Field(ge=0, le=1, allow_inf_nan=False)


class FieldMetricSummary(ContractModel):
    exact: MatchMetricSummary
    normalized: MatchMetricSummary


class GeometryMetricSummary(ContractModel):
    match: MatchMetricSummary
    comparable_regions: int = Field(ge=0)
    mean_iou: float = Field(ge=0, le=1, allow_inf_nan=False)


class CitationMetricSummary(ContractModel):
    correct: int = Field(ge=0)
    predicted: int = Field(ge=0)
    expected: int = Field(ge=0)
    correctness: float = Field(ge=0, le=1, allow_inf_nan=False)
    completeness: float = Field(ge=0, le=1, allow_inf_nan=False)
    f1: float = Field(ge=0, le=1, allow_inf_nan=False)


class ProvenanceMetricSummary(ContractModel):
    predicted_observations: int = Field(ge=0)
    observations_with_evidence: int = Field(ge=0)
    completeness: float = Field(ge=0, le=1, allow_inf_nan=False)


class NumericalMetricSummary(ContractModel):
    comparable_values: int = Field(ge=0)
    mean_absolute_error: float = Field(ge=0, allow_inf_nan=False)
    root_mean_squared_error: float = Field(ge=0, allow_inf_nan=False)
    mean_relative_error: float = Field(ge=0, allow_inf_nan=False)
    within_tolerance: float = Field(ge=0, le=1, allow_inf_nan=False)


class RankingMetricSummary(ContractModel):
    queries: int = Field(ge=0)
    recall_at_k: float = Field(ge=0, le=1, allow_inf_nan=False)
    mean_reciprocal_rank: float = Field(ge=0, le=1, allow_inf_nan=False)
    ndcg_at_k: float = Field(ge=0, le=1, allow_inf_nan=False)


class EvaluationMetricSummary(ContractModel):
    fields: FieldMetricSummary
    geometry: GeometryMetricSummary
    relationships: MatchMetricSummary
    citations: CitationMetricSummary
    provenance: ProvenanceMetricSummary
    numerical: NumericalMetricSummary
    ranking: RankingMetricSummary
    composite_score: float = Field(ge=0, le=1, allow_inf_nan=False)


class CalibrationCategory(StrEnum):
    FIELD = "field"
    GEOMETRY = "geometry"
    RELATIONSHIP = "relationship"
    NUMERICAL = "numerical"
    RANKING = "ranking"


class ConfidenceOutcome(ContractModel):
    case_id: UUID
    category: CalibrationCategory
    observation_key: str = Field(min_length=1, max_length=1000)
    confidence: float = Field(ge=0, le=1, allow_inf_nan=False)
    correct: bool


class CalibrationBucket(ContractModel):
    lower_bound: float = Field(ge=0, le=1, allow_inf_nan=False)
    upper_bound: float = Field(ge=0, le=1, allow_inf_nan=False)
    count: int = Field(ge=0)
    average_confidence: float = Field(ge=0, le=1, allow_inf_nan=False)
    accuracy: float = Field(ge=0, le=1, allow_inf_nan=False)


class CoveragePoint(ContractModel):
    threshold: float = Field(ge=0, le=1, allow_inf_nan=False)
    retained: int = Field(ge=0)
    coverage: float = Field(ge=0, le=1, allow_inf_nan=False)
    precision: float = Field(ge=0, le=1, allow_inf_nan=False)
    risk: float = Field(ge=0, le=1, allow_inf_nan=False)


class CalibrationSummary(ContractModel):
    observations: int = Field(ge=0)
    brier_score: float = Field(ge=0, le=1, allow_inf_nan=False)
    expected_calibration_error: float = Field(ge=0, le=1, allow_inf_nan=False)
    buckets: tuple[CalibrationBucket, ...]
    coverage_curve: tuple[CoveragePoint, ...]


class EvaluationCaseMetricResult(ContractModel):
    case_id: UUID
    dimensions: EvaluationCaseDimensions
    metrics: EvaluationMetricSummary
    confidence_outcomes: tuple[ConfidenceOutcome, ...]


class EvaluationMetricGroupResult(ContractModel):
    dimension: EvaluationGroupDimension
    value: Identifier
    case_count: int = Field(ge=1)
    metrics: EvaluationMetricSummary
    calibration: CalibrationSummary


class EvaluationMetricRunRecord(ContractModel):
    metric_run_id: UUID
    dataset_snapshot_id: UUID
    dataset_name: Identifier
    dataset_version: Identifier
    dataset_sha256: Sha256Digest
    scorer: ToolIdentifier
    pipeline: PipelineIdentifier
    model_versions: tuple[ModelIdentifier, ...]
    configuration: EvaluationMetricConfiguration
    input_sha256: Sha256Digest
    overall: EvaluationMetricSummary
    calibration: CalibrationSummary
    groups: tuple[EvaluationMetricGroupResult, ...]
    cases: tuple[EvaluationCaseMetricResult, ...]
    created_at: AwareDatetime

    @model_validator(mode="after")
    def result_is_unique(self) -> Self:
        _require_unique((item.case_id for item in self.cases), "metric case IDs")
        _require_unique(
            ((item.dimension, item.value) for item in self.groups),
            "metric group keys",
        )
        return self


class CalibrationSlice(ContractModel):
    metric_run_id: UUID
    category: CalibrationCategory | None
    calibration: CalibrationSummary


class ComparisonOutcome(StrEnum):
    REGRESSION = "regression"
    UNCHANGED = "unchanged"
    IMPROVEMENT = "improvement"


class EvaluationCaseComparison(ContractModel):
    case_id: UUID
    baseline_score: float = Field(ge=0, le=1, allow_inf_nan=False)
    candidate_score: float = Field(ge=0, le=1, allow_inf_nan=False)
    delta: float = Field(ge=-1, le=1, allow_inf_nan=False)
    outcome: ComparisonOutcome


class PipelineComparisonRecord(ContractModel):
    comparison_id: UUID
    dataset_snapshot_id: UUID
    dataset_name: Identifier
    dataset_version: Identifier
    dataset_sha256: Sha256Digest
    baseline_metric_run_id: UUID
    baseline_pipeline: PipelineIdentifier
    candidate_metric_run_id: UUID
    candidate_pipeline: PipelineIdentifier
    minimum_delta: float = Field(ge=0, le=1, allow_inf_nan=False)
    overall_delta: float = Field(ge=-1, le=1, allow_inf_nan=False)
    cases: tuple[EvaluationCaseComparison, ...]
    regressions: tuple[UUID, ...]
    improvements: tuple[UUID, ...]
    created_at: AwareDatetime


def evaluation_dataset_content_sha256(cases: tuple[EvaluationReferenceCase, ...]) -> str:
    payload = [
        case.model_dump(mode="json") for case in sorted(cases, key=lambda item: str(item.case_id))
    ]
    encoded = json.dumps(payload, sort_keys=True, separators=(",", ":")).encode()
    return hashlib.sha256(encoded).hexdigest()


def _parse_json(value: str, label: str) -> object:
    try:
        return json.loads(value)
    except json.JSONDecodeError as exc:
        raise ValueError(f"{label} must contain valid JSON") from exc


def _require_unique(values: Iterable[Hashable], label: str) -> None:
    materialized = tuple(values)
    if len(materialized) != len(set(materialized)):
        raise ValueError(f"{label} must be unique")
