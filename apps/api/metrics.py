"""Versioned evaluation scoring, calibration, and pipeline comparison API."""

from __future__ import annotations

from typing import Annotated, NoReturn
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query, status
from hiveblot_contracts import (
    BoundingRegion,
    CalibrationCategory,
    CalibrationSlice,
    EvaluationCaseDimensions,
    EvaluationCaseMetricResult,
    EvaluationDatasetSnapshot,
    EvaluationGroupDimension,
    EvaluationMetricConfiguration,
    EvaluationMetricGroupResult,
    EvaluationMetricRunRecord,
    EvaluationReferenceCase,
    EvaluationScoringInput,
    ModelIdentifier,
    PipelineComparisonRecord,
    PipelineEvaluationCase,
    PipelineEvaluationSubmission,
    PipelineIdentifier,
    PredictedFieldObservation,
    PredictedNumericalObservation,
    PredictedObservationDocument,
    PredictedRankingResult,
    PredictedRegionObservation,
    PredictedRelationshipObservation,
    RankingJudgment,
    ReferenceFieldObservation,
    ReferenceNumericalObservation,
    ReferenceObservationDocument,
    ReferenceRegionObservation,
    ReferenceRelationshipObservation,
    ReviewStatus,
    SpatialAnnotationType,
    ToolIdentifier,
)
from hiveblot_evaluation import (
    DuplicateEvaluationRecord,
    EvaluationError,
    EvaluationMetricsService,
    EvaluationNotFound,
    InvalidEvaluationState,
)
from pydantic import BaseModel, ValidationError

from .evaluation_schemas import BoundingRegionInput
from .metrics_dependencies import get_evaluation_metrics_service
from .metrics_schemas import (
    CalibrationResponse,
    CalibrationSliceResponse,
    CaseComparisonResponse,
    CompareMetricRunsRequest,
    ConfidenceOutcomeResponse,
    CreateMetricRunRequest,
    EvaluationDimensionsInput,
    MetricCaseDetailResponse,
    MetricCaseSummaryResponse,
    MetricConfigurationInput,
    MetricGroupResponse,
    MetricRunListResponse,
    MetricRunResponse,
    MetricSummaryResponse,
    ModelVersionResponse,
    PipelineComparisonResponse,
    PredictedObservationInput,
    ReferenceObservationInput,
)

router = APIRouter(prefix="/api/v1", tags=["evaluation metrics"])
MetricsServiceDependency = Annotated[
    EvaluationMetricsService,
    Depends(get_evaluation_metrics_service),
]


@router.post(
    "/evaluation-metric-runs",
    response_model=MetricRunResponse,
    status_code=status.HTTP_201_CREATED,
)
def create_metric_run(
    request: CreateMetricRunRequest,
    service: MetricsServiceDependency,
) -> MetricRunResponse:
    try:
        record = service.score(_scoring_input(request))
    except (EvaluationError, ValidationError) as exc:
        _raise_http(exc)
    return _run_response(record)


@router.get("/evaluation-metric-runs", response_model=MetricRunListResponse)
def list_metric_runs(
    service: MetricsServiceDependency,
    dataset_name: str | None = None,
    dataset_version: str | None = None,
    pipeline_name: str | None = None,
    pipeline_version: str | None = None,
    limit: Annotated[int, Query(ge=1, le=200)] = 50,
) -> MetricRunListResponse:
    try:
        records = service.list(
            dataset_name=dataset_name,
            dataset_version=dataset_version,
            pipeline_name=pipeline_name,
            pipeline_version=pipeline_version,
            limit=limit,
        )
    except (EvaluationError, ValidationError) as exc:
        _raise_http(exc)
    return MetricRunListResponse(runs=tuple(_run_response(record) for record in records))


@router.get(
    "/evaluation-metric-runs/{metric_run_id}",
    response_model=MetricRunResponse,
)
def get_metric_run(
    metric_run_id: UUID,
    service: MetricsServiceDependency,
) -> MetricRunResponse:
    try:
        record = service.get(metric_run_id)
    except (EvaluationError, ValidationError) as exc:
        _raise_http(exc)
    return _run_response(record)


@router.get(
    "/evaluation-metric-runs/{metric_run_id}/cases/{case_id}",
    response_model=MetricCaseDetailResponse,
)
def get_metric_case(
    metric_run_id: UUID,
    case_id: UUID,
    service: MetricsServiceDependency,
) -> MetricCaseDetailResponse:
    try:
        record = service.get_case(metric_run_id, case_id)
    except (EvaluationError, ValidationError) as exc:
        _raise_http(exc)
    return _case_detail_response(record)


@router.get(
    "/evaluation-metric-runs/{metric_run_id}/calibration",
    response_model=CalibrationSliceResponse,
)
def get_metric_calibration(
    metric_run_id: UUID,
    service: MetricsServiceDependency,
    category: CalibrationCategory | None = None,
) -> CalibrationSliceResponse:
    try:
        record = service.calibration(metric_run_id, category=category)
    except (EvaluationError, ValidationError) as exc:
        _raise_http(exc)
    return _calibration_slice_response(record)


@router.post(
    "/evaluation-pipeline-comparisons",
    response_model=PipelineComparisonResponse,
    status_code=status.HTTP_201_CREATED,
)
def compare_pipeline_runs(
    request: CompareMetricRunsRequest,
    service: MetricsServiceDependency,
) -> PipelineComparisonResponse:
    try:
        comparison = service.compare(
            request.baseline_metric_run_id,
            request.candidate_metric_run_id,
            minimum_delta=request.minimum_delta,
        )
    except (EvaluationError, ValidationError) as exc:
        _raise_http(exc)
    return _comparison_response(comparison)


def _scoring_input(request: CreateMetricRunRequest) -> EvaluationScoringInput:
    dataset = EvaluationDatasetSnapshot(
        snapshot_id=request.dataset.snapshot_id,
        dataset_name=request.dataset.dataset_name,
        dataset_version=request.dataset.dataset_version,
        content_sha256=request.dataset.content_sha256,
        frozen_at=request.dataset.frozen_at,
        cases=tuple(
            EvaluationReferenceCase(
                case_id=item.case_id,
                dimensions=_dimensions(item.dimensions),
                reference=_reference(item.reference),
            )
            for item in request.dataset.cases
        ),
    )
    submission = PipelineEvaluationSubmission(
        pipeline=PipelineIdentifier(
            name=request.submission.pipeline_name,
            version=request.submission.pipeline_version,
        ),
        model_versions=tuple(
            ModelIdentifier(provider=item.provider, name=item.name, version=item.version)
            for item in request.submission.model_versions
        ),
        cases=tuple(
            PipelineEvaluationCase(
                case_id=item.case_id,
                publication_ids=item.publication_ids,
                prediction=_prediction(item.prediction),
            )
            for item in request.submission.cases
        ),
    )
    return EvaluationScoringInput(
        dataset=dataset,
        submission=submission,
        scorer=ToolIdentifier(name=request.scorer_name, version=request.scorer_version),
        configuration=_configuration(request.configuration),
    )


def _dimensions(value: EvaluationDimensionsInput) -> EvaluationCaseDimensions:
    return EvaluationCaseDimensions(
        source_type=value.source_type,
        journal=value.journal,
        repository=value.repository,
        image_quality=value.image_quality,
        assay_layout=value.assay_layout,
        review_status=ReviewStatus(value.review_status),
    )


def _reference(value: ReferenceObservationInput) -> ReferenceObservationDocument:
    return ReferenceObservationDocument(
        fields=tuple(
            ReferenceFieldObservation(
                field_path=item.field_path,
                value_json=item.value_json,
                evidence_artifact_ids=item.evidence_artifact_ids,
            )
            for item in value.fields
        ),
        regions=tuple(
            ReferenceRegionObservation(
                region_key=item.region_key,
                annotation_type=SpatialAnnotationType(item.annotation_type),
                region=_region(item.region),
                evidence_artifact_ids=item.evidence_artifact_ids,
            )
            for item in value.regions
        ),
        relationships=tuple(
            ReferenceRelationshipObservation(
                subject_key=item.subject_key,
                relation_type=item.relation_type,
                object_key=item.object_key,
                evidence_artifact_ids=item.evidence_artifact_ids,
            )
            for item in value.relationships
        ),
        numerical_values=tuple(
            ReferenceNumericalObservation(
                field_path=item.field_path,
                value=item.value,
                unit=item.unit,
                evidence_artifact_ids=item.evidence_artifact_ids,
            )
            for item in value.numerical_values
        ),
        ranking_judgments=tuple(
            RankingJudgment(
                query_id=item.query_id,
                item_id=item.item_id,
                relevance=item.relevance,
            )
            for item in value.ranking_judgments
        ),
    )


def _prediction(value: PredictedObservationInput) -> PredictedObservationDocument:
    return PredictedObservationDocument(
        fields=tuple(
            PredictedFieldObservation(
                field_path=item.field_path,
                value_json=item.value_json,
                evidence_artifact_ids=item.evidence_artifact_ids,
                confidence=item.confidence,
            )
            for item in value.fields
        ),
        regions=tuple(
            PredictedRegionObservation(
                region_key=item.region_key,
                annotation_type=SpatialAnnotationType(item.annotation_type),
                region=_region(item.region),
                evidence_artifact_ids=item.evidence_artifact_ids,
                confidence=item.confidence,
            )
            for item in value.regions
        ),
        relationships=tuple(
            PredictedRelationshipObservation(
                subject_key=item.subject_key,
                relation_type=item.relation_type,
                object_key=item.object_key,
                evidence_artifact_ids=item.evidence_artifact_ids,
                confidence=item.confidence,
            )
            for item in value.relationships
        ),
        numerical_values=tuple(
            PredictedNumericalObservation(
                field_path=item.field_path,
                value=item.value,
                unit=item.unit,
                evidence_artifact_ids=item.evidence_artifact_ids,
                confidence=item.confidence,
            )
            for item in value.numerical_values
        ),
        ranked_results=tuple(
            PredictedRankingResult(
                query_id=item.query_id,
                item_id=item.item_id,
                rank=item.rank,
                score=item.score,
                confidence=item.confidence,
            )
            for item in value.ranked_results
        ),
    )


def _region(value: BoundingRegionInput) -> BoundingRegion:
    # BoundingRegionInput and its canonical counterpart intentionally have the
    # same fields, but this adapter prevents HTTP models becoming domain models.
    fields = value.model_dump(mode="python", exclude={"schema_version"})
    return BoundingRegion(**fields)


def _configuration(value: MetricConfigurationInput) -> EvaluationMetricConfiguration:
    return EvaluationMetricConfiguration(
        bounding_box_iou_threshold=value.bounding_box_iou_threshold,
        numerical_absolute_tolerance=value.numerical_absolute_tolerance,
        ranking_k=value.ranking_k,
        calibration_bins=value.calibration_bins,
        confidence_thresholds=value.confidence_thresholds,
        normalized_string_casefold=value.normalized_string_casefold,
        group_by=tuple(EvaluationGroupDimension(item) for item in value.group_by),
    )


def _run_response(value: EvaluationMetricRunRecord) -> MetricRunResponse:
    return MetricRunResponse(
        metric_run_id=value.metric_run_id,
        dataset_snapshot_id=value.dataset_snapshot_id,
        dataset_name=value.dataset_name,
        dataset_version=value.dataset_version,
        dataset_sha256=value.dataset_sha256,
        scorer_name=value.scorer.name,
        scorer_version=value.scorer.version,
        pipeline_name=value.pipeline.name,
        pipeline_version=value.pipeline.version,
        model_versions=tuple(
            ModelVersionResponse(provider=item.provider, name=item.name, version=item.version)
            for item in value.model_versions
        ),
        input_sha256=value.input_sha256,
        configuration=MetricConfigurationInput.model_validate(
            _contract_payload(value.configuration)
        ),
        overall=_response_from_contract(value.overall, MetricSummaryResponse),
        calibration=_response_from_contract(value.calibration, CalibrationResponse),
        groups=tuple(_group_response(item) for item in value.groups),
        cases=tuple(_case_summary_response(item) for item in value.cases),
        created_at=value.created_at,
    )


def _group_response(value: EvaluationMetricGroupResult) -> MetricGroupResponse:
    return MetricGroupResponse.model_validate(_contract_payload(value))


def _case_summary_response(value: EvaluationCaseMetricResult) -> MetricCaseSummaryResponse:
    return MetricCaseSummaryResponse(
        case_id=value.case_id,
        dimensions=EvaluationDimensionsInput.model_validate(_contract_payload(value.dimensions)),
        metrics=_response_from_contract(value.metrics, MetricSummaryResponse),
        workbench_url=f"/workbench/{value.case_id}",
    )


def _case_detail_response(value: EvaluationCaseMetricResult) -> MetricCaseDetailResponse:
    summary = _case_summary_response(value)
    return MetricCaseDetailResponse(
        **summary.model_dump(mode="python", exclude={"schema_version"}),
        confidence_outcomes=tuple(
            ConfidenceOutcomeResponse(
                category=item.category.value,
                observation_key=item.observation_key,
                confidence=item.confidence,
                correct=item.correct,
            )
            for item in value.confidence_outcomes
        ),
    )


def _calibration_slice_response(value: CalibrationSlice) -> CalibrationSliceResponse:
    return CalibrationSliceResponse(
        metric_run_id=value.metric_run_id,
        category=value.category.value if value.category is not None else None,
        calibration=_response_from_contract(value.calibration, CalibrationResponse),
    )


def _comparison_response(value: PipelineComparisonRecord) -> PipelineComparisonResponse:
    return PipelineComparisonResponse(
        comparison_id=value.comparison_id,
        dataset_snapshot_id=value.dataset_snapshot_id,
        dataset_name=value.dataset_name,
        dataset_version=value.dataset_version,
        dataset_sha256=value.dataset_sha256,
        baseline_metric_run_id=value.baseline_metric_run_id,
        baseline_pipeline_name=value.baseline_pipeline.name,
        baseline_pipeline_version=value.baseline_pipeline.version,
        candidate_metric_run_id=value.candidate_metric_run_id,
        candidate_pipeline_name=value.candidate_pipeline.name,
        candidate_pipeline_version=value.candidate_pipeline.version,
        minimum_delta=value.minimum_delta,
        overall_delta=value.overall_delta,
        cases=tuple(
            CaseComparisonResponse(
                case_id=item.case_id,
                baseline_score=item.baseline_score,
                candidate_score=item.candidate_score,
                delta=item.delta,
                outcome=item.outcome.value,
                workbench_url=f"/workbench/{item.case_id}",
            )
            for item in value.cases
        ),
        regression_case_urls=tuple(f"/workbench/{case_id}" for case_id in value.regressions),
        improvement_case_urls=tuple(f"/workbench/{case_id}" for case_id in value.improvements),
        created_at=value.created_at,
    )


def _contract_payload(value: BaseModel) -> dict[str, object]:
    return value.model_dump(mode="python", exclude={"schema_version"})


def _response_from_contract[T: BaseModel](value: BaseModel, response_type: type[T]) -> T:
    return response_type.model_validate(_contract_payload(value))


def _raise_http(exc: EvaluationError | ValidationError) -> NoReturn:
    if isinstance(exc, EvaluationNotFound):
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc)) from exc
    if isinstance(exc, DuplicateEvaluationRecord):
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(exc)) from exc
    if isinstance(exc, (InvalidEvaluationState, ValidationError)):
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
            detail=str(exc),
        ) from exc
    raise HTTPException(status_code=500, detail="evaluation metric operation failed") from exc
