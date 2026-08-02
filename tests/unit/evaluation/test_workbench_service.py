from datetime import UTC, datetime
from uuid import uuid4

import pytest
from hiveblot_contracts import (
    ArtifactAcquisitionMethod,
    ArtifactRecord,
    ArtifactVisibility,
    BoundingRegion,
    CaseArtifactRole,
    CaseSourceArtifact,
    FieldAnnotation,
    FieldPathTarget,
    ModelIdentifier,
    ObservationState,
    PredictionEvidence,
    ReviewStatus,
    SpatialAnnotation,
    SpatialAnnotationType,
)
from hiveblot_evaluation import (
    ConcurrencyConflict,
    EvaluationService,
    EvidenceWorkbenchService,
    InvalidEvaluationState,
)

from tests.fakes.evaluation import InMemoryEvaluationRepository
from tests.fakes.workbench import InMemoryArtifactLookup, InMemorySourceContextRepository

NOW = datetime(2026, 8, 2, 19, tzinfo=UTC)


def test_workbench_aggregates_distinct_prediction_reviewer_and_adjudication_overlays() -> None:
    artifact = _artifact()
    evaluation = EvaluationService(InMemoryEvaluationRepository(), clock=lambda: NOW)
    case = evaluation.create_case(
        case_key="paper:figure-2:B",
        dataset_id=None,
        source_artifacts=(
            CaseSourceArtifact(artifact_id=artifact.artifact_id, role=CaseArtifactRole.FIGURE),
        ),
    )
    region = _region(artifact.artifact_id)
    prediction = evaluation.add_prediction(
        case.case_id,
        prediction_schema="western-blot-extraction",
        prediction_schema_version="1.0",
        producer=ModelIdentifier(provider="local", name="vision", version="1"),
        pipeline=None,
        raw_output_json='{"target":"p53"}',
        normalized_output_json='{"target":"p53"}',
        configuration_json="{}",
        evidence=(
            PredictionEvidence(
                artifact_id=artifact.artifact_id,
                field_path="/targets/0/name",
                region_id=region.region_id,
                region=region,
                description="predicted blot",
            ),
        ),
        validation_issues=(),
        confidence=0.8,
        trace_id=uuid4(),
        latency_ms=100,
        cost_microusd=0,
    )
    revisions = []
    for reviewer_id in (uuid4(), uuid4()):
        _, revision = evaluation.create_annotation(
            case.case_id,
            reviewer_id=reviewer_id,
            rationale=None,
            error_codes=(),
            field_annotations=(
                FieldAnnotation(
                    field_annotation_id=uuid4(),
                    target=FieldPathTarget(field_path="/targets/0/name"),
                    state=ObservationState.PRESENT,
                    value="TP53",
                    evidence_region_ids=(region.region_id,),
                ),
            ),
            spatial_annotations=(
                SpatialAnnotation(
                    spatial_annotation_id=region.region_id,
                    annotation_type=SpatialAnnotationType.BLOT,
                    state=ObservationState.PRESENT,
                    region=region,
                    label="TP53",
                ),
            ),
            relationships=(),
        )
        revisions.append(revision)
    current = evaluation.get_case(case.case_id)
    evaluation.update_case_status(
        case.case_id,
        expected_version=current.version,
        review_status=ReviewStatus.NEEDS_ADJUDICATION,
    )
    adjudication = evaluation.adjudicate(
        case.case_id,
        adjudicator_id=uuid4(),
        selected_revision_id=revisions[0].revision_id,
        considered_revision_ids=tuple(revision.revision_id for revision in revisions),
        rationale="Both reviewers agree.",
    )
    service = EvidenceWorkbenchService(
        evaluation,
        InMemoryArtifactLookup((artifact,)),
        InMemorySourceContextRepository(),
    )
    service.put_source_context(
        case.case_id,
        artifact.artifact_id,
        CaseArtifactRole.FIGURE,
        expected_head_revision_id=None,
        caption="Figure 2. TP53 response.",
        nearby_text="Cells were treated for 24 hours.",
    )

    workbench = service.get_workbench(case.case_id)

    assert workbench.sources[0].artifact.sha256 == "a" * 64
    assert workbench.sources[0].caption == "Figure 2. TP53 response."
    assert [overlay.overlay_source for overlay in workbench.overlays] == [
        "prediction",
        "reviewer",
        "reviewer",
        "adjudication",
    ]
    assert workbench.overlays[0].prediction_id == prediction.prediction_id
    assert workbench.overlays[-1].adjudication_id == adjudication.adjudication_id
    assert all("/targets/0/name" in overlay.linked_field_keys for overlay in workbench.overlays)


def test_source_context_must_use_case_artifact_and_include_text() -> None:
    artifact = _artifact()
    evaluation = EvaluationService(InMemoryEvaluationRepository(), clock=lambda: NOW)
    case = evaluation.create_case(
        case_key="paper:figure-3:A",
        dataset_id=None,
        source_artifacts=(
            CaseSourceArtifact(artifact_id=artifact.artifact_id, role=CaseArtifactRole.FIGURE),
        ),
    )
    service = EvidenceWorkbenchService(
        evaluation,
        InMemoryArtifactLookup((artifact,)),
        InMemorySourceContextRepository(),
    )

    with pytest.raises(InvalidEvaluationState, match="case artifact association"):
        service.put_source_context(
            case.case_id,
            uuid4(),
            CaseArtifactRole.FIGURE,
            expected_head_revision_id=None,
            caption="wrong artifact",
            nearby_text=None,
        )
    with pytest.raises(InvalidEvaluationState, match="requires a caption"):
        service.put_source_context(
            case.case_id,
            artifact.artifact_id,
            CaseArtifactRole.FIGURE,
            expected_head_revision_id=None,
            caption="  ",
            nearby_text=None,
        )

    first = service.put_source_context(
        case.case_id,
        artifact.artifact_id,
        CaseArtifactRole.FIGURE,
        expected_head_revision_id=None,
        caption="Initial caption",
        nearby_text=None,
    )
    second = service.put_source_context(
        case.case_id,
        artifact.artifact_id,
        CaseArtifactRole.FIGURE,
        expected_head_revision_id=first.context_revision_id,
        caption="Corrected caption",
        nearby_text=None,
    )
    history = service.list_source_context_revisions(
        case.case_id,
        artifact.artifact_id,
        CaseArtifactRole.FIGURE,
    )
    assert history == (first, second)
    assert second.prior_revision_id == first.context_revision_id
    with pytest.raises(ConcurrencyConflict):
        service.put_source_context(
            case.case_id,
            artifact.artifact_id,
            CaseArtifactRole.FIGURE,
            expected_head_revision_id=first.context_revision_id,
            caption="Stale overwrite",
            nearby_text=None,
        )


def _artifact() -> ArtifactRecord:
    return ArtifactRecord(
        artifact_id=uuid4(),
        sha256="a" * 64,
        media_type="image/png",
        byte_size=2048,
        original_filename="figure-2.png",
        source_uri="https://repository.example/figure-2.png",
        acquisition_method=ArtifactAcquisitionMethod.SOURCE_ADAPTER,
        visibility=ArtifactVisibility.PUBLIC,
        created_at=NOW,
    )


def _region(artifact_id):
    return BoundingRegion(
        region_id=uuid4(),
        source_artifact_id=artifact_id,
        x=20.0,
        y=15.0,
        width=80.0,
        height=30.0,
        canvas_width=400,
        canvas_height=200,
    )
