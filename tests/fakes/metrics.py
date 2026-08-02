"""Deterministic frozen evaluation inputs for metric and calibration tests."""

from datetime import UTC, datetime
from uuid import UUID

from hiveblot_contracts import (
    BoundingRegion,
    EvaluationCaseDimensions,
    EvaluationDatasetSnapshot,
    EvaluationMetricConfiguration,
    EvaluationReferenceCase,
    EvaluationScoringInput,
    ModelIdentifier,
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
    evaluation_dataset_content_sha256,
)

NOW = datetime(2026, 8, 2, 23, tzinfo=UTC)
SNAPSHOT_ID = UUID("10000000-0000-0000-0000-000000000001")
CASE_ONE = UUID("20000000-0000-0000-0000-000000000001")
CASE_TWO = UUID("20000000-0000-0000-0000-000000000002")
ARTIFACT_ONE = UUID("30000000-0000-0000-0000-000000000001")
ARTIFACT_TWO = UUID("30000000-0000-0000-0000-000000000002")


def scoring_input(*, candidate: bool = False) -> EvaluationScoringInput:
    reference_cases = (
        _reference_case(CASE_ONE, ARTIFACT_ONE),
        _reference_case(CASE_TWO, ARTIFACT_TWO),
    )
    dataset = EvaluationDatasetSnapshot(
        snapshot_id=SNAPSHOT_ID,
        dataset_name="western-blot-frozen",
        dataset_version="2026.08",
        content_sha256=evaluation_dataset_content_sha256(reference_cases),
        frozen_at=NOW,
        cases=reference_cases,
    )
    return EvaluationScoringInput(
        dataset=dataset,
        submission=PipelineEvaluationSubmission(
            pipeline=PipelineIdentifier(
                name="western-blot-extraction",
                version="2.0" if candidate else "1.0",
            ),
            model_versions=(
                ModelIdentifier(
                    provider="local",
                    name="western-blot-model",
                    version="2.0" if candidate else "1.0",
                ),
            ),
            cases=(
                _prediction_case(CASE_ONE, ARTIFACT_ONE, candidate=candidate, first=True),
                _prediction_case(CASE_TWO, ARTIFACT_TWO, candidate=candidate, first=False),
            ),
        ),
        scorer=ToolIdentifier(name="hiveblot-evaluator", version="1.0"),
        configuration=EvaluationMetricConfiguration(
            bounding_box_iou_threshold=0.5,
            numerical_absolute_tolerance=2.0,
            ranking_k=2,
            calibration_bins=5,
            confidence_thresholds=(0.0, 0.5, 0.8, 0.95),
        ),
    )


def _reference_case(case_id: UUID, artifact_id: UUID) -> EvaluationReferenceCase:
    target = "TP53" if case_id == CASE_ONE else "AKT1"
    return EvaluationReferenceCase(
        case_id=case_id,
        dimensions=EvaluationCaseDimensions(
            source_type="publication_figure" if case_id == CASE_ONE else "raw_source",
            journal="Journal of Reproducible Blots",
            repository="PMC",
            image_quality="compressed" if case_id == CASE_ONE else "source_quality",
            assay_layout="single_panel",
            review_status=ReviewStatus.ADJUDICATED,
        ),
        reference=ReferenceObservationDocument(
            fields=(
                ReferenceFieldObservation(
                    field_path="/target",
                    value_json=f'"{target}"',
                    evidence_artifact_ids=(artifact_id,),
                ),
            ),
            regions=(
                ReferenceRegionObservation(
                    region_key="lane-1",
                    annotation_type=SpatialAnnotationType.LANE,
                    region=_region(artifact_id, x=10),
                    evidence_artifact_ids=(artifact_id,),
                ),
            ),
            relationships=(
                ReferenceRelationshipObservation(
                    subject_key="lane-1",
                    relation_type="contains",
                    object_key="target-1",
                    evidence_artifact_ids=(artifact_id,),
                ),
            ),
            numerical_values=(
                ReferenceNumericalObservation(
                    field_path="/intensity",
                    value=100.0,
                    unit="a.u.",
                    evidence_artifact_ids=(artifact_id,),
                ),
            ),
            ranking_judgments=(
                RankingJudgment(query_id="target-search", item_id=target, relevance=3),
                RankingJudgment(query_id="target-search", item_id="negative", relevance=0),
            ),
        ),
    )


def _prediction_case(
    case_id: UUID,
    artifact_id: UUID,
    *,
    candidate: bool,
    first: bool,
) -> PipelineEvaluationCase:
    if first:
        target = "TP53" if candidate else "  tp53  "
        region_x = 11 if candidate else 17
        include_relationship = candidate
        evidence = (artifact_id,)
        intensity = 100.5 if candidate else 101.0
        ranked_target = "TP53"
    elif candidate:
        target = "ERK1"
        region_x = 27
        include_relationship = False
        evidence = ()
        intensity = 110.0
        ranked_target = "negative"
    else:
        target = "AKT1"
        region_x = 10
        include_relationship = True
        evidence = (artifact_id,)
        intensity = 100.0
        ranked_target = "AKT1"
    relationships = (
        (
            PredictedRelationshipObservation(
                subject_key="lane-1",
                relation_type="contains",
                object_key="target-1",
                evidence_artifact_ids=evidence,
                confidence=0.85,
            ),
        )
        if include_relationship
        else ()
    )
    if candidate and not first:
        relationships = (
            PredictedRelationshipObservation(
                subject_key="lane-1",
                relation_type="contains",
                object_key="wrong-target",
                confidence=0.8,
            ),
        )
    return PipelineEvaluationCase(
        case_id=case_id,
        publication_ids=(
            UUID(
                "40000000-0000-0000-0000-000000000001"
                if first
                else "40000000-0000-0000-0000-000000000002"
            ),
        ),
        prediction=PredictedObservationDocument(
            fields=(
                PredictedFieldObservation(
                    field_path="/target",
                    value_json=f'"{target}"',
                    evidence_artifact_ids=evidence,
                    confidence=0.9 if first else 0.82,
                ),
            ),
            regions=(
                PredictedRegionObservation(
                    region_key="lane-1",
                    annotation_type=SpatialAnnotationType.LANE,
                    region=_region(artifact_id, x=region_x),
                    evidence_artifact_ids=evidence,
                    confidence=0.75,
                ),
            ),
            relationships=relationships,
            numerical_values=(
                PredictedNumericalObservation(
                    field_path="/intensity",
                    value=intensity,
                    unit="a.u.",
                    evidence_artifact_ids=evidence,
                    confidence=0.8,
                ),
            ),
            ranked_results=(
                PredictedRankingResult(
                    query_id="target-search",
                    item_id=ranked_target,
                    rank=1,
                    score=0.9,
                    confidence=0.9,
                ),
                PredictedRankingResult(
                    query_id="target-search",
                    item_id="negative" if ranked_target != "negative" else "other",
                    rank=2,
                    score=0.2,
                    confidence=0.3,
                ),
            ),
        ),
    )


def _region(artifact_id: UUID, *, x: float) -> BoundingRegion:
    return BoundingRegion(
        region_id=UUID(f"50000000-0000-0000-0000-{int(x):012d}"),
        source_artifact_id=artifact_id,
        x=x,
        y=10,
        width=10,
        height=10,
        canvas_width=100,
        canvas_height=100,
    )
