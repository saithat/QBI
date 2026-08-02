import json

import pytest
from hiveblot_contracts import (
    ArtifactReference,
    BoundingRegion,
    SpatialAnnotationType,
    WesternBlotExtractionConfiguration,
    WesternBlotExtractionInput,
    WesternBlotFigureCandidate,
    WesternBlotFigureCandidateSet,
    WesternBlotSourceKind,
)
from hiveblot_extraction import (
    ExtractionOutputInvalid,
    assemble_extraction_result,
    normalize_candidate_predictions,
)

from tests.fakes.extraction import (
    FixtureExtractionImplementation,
    extraction_artifact,
    fixture_candidate_id,
)


def test_historic_model_output_enters_strict_reviewable_contracts() -> None:
    artifact = extraction_artifact()
    implementation = FixtureExtractionImplementation()
    reference = ArtifactReference(
        artifact_id=artifact.artifact_id,
        sha256=artifact.sha256,
        media_type=artifact.media_type,
        byte_size=artifact.byte_size,
    )
    candidate_id = fixture_candidate_id(artifact.artifact_id)
    region = BoundingRegion(
        region_id=candidate_id,
        source_artifact_id=artifact.artifact_id,
        x=0.0,
        y=288.0,
        width=2696.0,
        height=2658.0,
        canvas_width=2696,
        canvas_height=3500,
        page_number=4,
    )
    candidates = WesternBlotFigureCandidateSet(
        source_artifact=reference,
        source_kind=WesternBlotSourceKind.PDF,
        detector=implementation.identity.detector,
        candidates=(
            WesternBlotFigureCandidate(
                candidate_id=candidate_id,
                source_artifact=reference,
                region=region,
                tight_region=region,
                detector_score=0.7784390975201184,
            ),
        ),
    )
    normalized = normalize_candidate_predictions(
        candidates,
        model=implementation.identity.model,
        prompt_version=implementation.identity.prompt_version,
        raw_responses={candidate_id: implementation.raw_output},
    )
    extraction_input = WesternBlotExtractionInput(
        source_artifact=reference,
        source_kind=WesternBlotSourceKind.PDF,
        configuration=WesternBlotExtractionConfiguration(
            implementation_name=implementation.identity.implementation_name
        ),
        trace_id=candidate_id,
    )
    result = assemble_extraction_result(
        case_id=extraction_input.trace_id,
        implementation=implementation.identity,
        predictions=normalized,
    )

    assert len(normalized.predictions[0].panels) == 3
    assert sum(len(panel.bands) for panel in normalized.predictions[0].panels) == 40
    assert {item.name for item in result.structured_annotation.proteins} == {
        "p53",
        "GAPDH",
        "Vinculin",
    }
    assert len(result.structured_annotation.lane_conditions) == 20
    assert (
        sum(
            item.annotation_type is SpatialAnnotationType.BAND
            for item in result.spatial_annotation_set.spatial_annotations
        )
        == 40
    )
    assert any(issue.code == "estimated_geometry" for issue in result.validation_issues)
    evidence_by_path = {item.field_path: item for item in result.field_evidence}
    first_band = normalized.predictions[0].panels[0].bands[0]
    assert evidence_by_path["/candidate_predictions/predictions/0/is_western_blot"].region_ids
    assert evidence_by_path[
        "/candidate_predictions/predictions/0/panels/0/bands/0/band_state"
    ].region_ids == (first_band.band_id,)
    assert json.loads(result.model_dump_json())["source_artifact"]["sha256"] == artifact.sha256


def test_cross_field_model_validation_failure_is_normalization_error() -> None:
    artifact = extraction_artifact()
    implementation = FixtureExtractionImplementation()
    reference = ArtifactReference(
        artifact_id=artifact.artifact_id,
        sha256=artifact.sha256,
        media_type=artifact.media_type,
        byte_size=artifact.byte_size,
    )
    candidate_id = fixture_candidate_id(artifact.artifact_id)
    region = BoundingRegion(
        region_id=candidate_id,
        source_artifact_id=artifact.artifact_id,
        x=0.0,
        y=0.0,
        width=100.0,
        height=100.0,
        canvas_width=100,
        canvas_height=100,
        page_number=1,
    )
    candidates = WesternBlotFigureCandidateSet(
        source_artifact=reference,
        source_kind=WesternBlotSourceKind.PDF,
        detector=implementation.identity.detector,
        candidates=(
            WesternBlotFigureCandidate(
                candidate_id=candidate_id,
                source_artifact=reference,
                region=region,
                detector_score=0.9,
            ),
        ),
    )
    incomplete_grid = json.dumps(
        {
            "is_western_blot": True,
            "targets_top_to_bottom": [
                {
                    "row_index": 1,
                    "target": "p53",
                    "is_loading_control": False,
                    "confidence": "high",
                }
            ],
            "lanes_left_to_right": [
                {"lane_index": 1, "condition": "control", "confidence": "high"},
                {"lane_index": 2, "condition": "treated", "confidence": "high"},
            ],
            "bands": [
                {
                    "row_index": 1,
                    "target": "p53",
                    "lane_index": 1,
                    "band_state": "present",
                    "confidence": "high",
                }
            ],
        }
    )

    with pytest.raises(ExtractionOutputInvalid, match="canonical western-blot validation"):
        normalize_candidate_predictions(
            candidates,
            model=implementation.identity.model,
            prompt_version=implementation.identity.prompt_version,
            raw_responses={candidate_id: incomplete_grid},
        )
