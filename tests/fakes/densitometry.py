from __future__ import annotations

import hashlib
import io
from uuid import UUID, uuid4

from hiveblot_contracts import (
    ArtifactReference,
    BoundingRegion,
    DensitometryBackgroundMethod,
    DensitometryBandRegion,
    DensitometryConfiguration,
    DensitometryImageKind,
    DensitometryInput,
    DensitometryLaneRegion,
    DensitometryNormalizationMethod,
    DensitometryTargetRegion,
    ObservationState,
    PredictionGeometryReference,
    SpatialAnnotation,
    SpatialAnnotationSet,
    SpatialAnnotationType,
    SpatialEditorRelationship,
    SpatialRelationshipType,
)
from PIL import Image, ImageDraw

CASE_ID = UUID("10000000-0000-0000-0000-000000000001")
ARTIFACT_ID = UUID("20000000-0000-0000-0000-000000000001")
PREDICTION_ID = UUID("30000000-0000-0000-0000-000000000001")
LANE_IDS = (
    UUID("40000000-0000-0000-0000-000000000001"),
    UUID("40000000-0000-0000-0000-000000000002"),
)
TARGET_IDS = (
    UUID("50000000-0000-0000-0000-000000000001"),
    UUID("50000000-0000-0000-0000-000000000002"),
)
BAND_IDS = (
    UUID("60000000-0000-0000-0000-000000000001"),
    UUID("60000000-0000-0000-0000-000000000002"),
    UUID("60000000-0000-0000-0000-000000000003"),
    UUID("60000000-0000-0000-0000-000000000004"),
)


def synthetic_blot_png() -> bytes:
    image = Image.new("L", (40, 24), 240)
    draw = ImageDraw.Draw(image)
    draw.rectangle((5, 4, 14, 7), fill=100)
    draw.rectangle((25, 4, 34, 7), fill=160)
    draw.rectangle((5, 16, 14, 19), fill=120)
    draw.rectangle((25, 16, 34, 19), fill=120)
    buffer = io.BytesIO()
    image.save(buffer, format="PNG", optimize=False, compress_level=9)
    return buffer.getvalue()


def synthetic_densitometry_input(
    *,
    publication: bool = False,
    loading_control_target_id: UUID | None = TARGET_IDS[1],
    first_band_x: float = 5.0,
) -> DensitometryInput:
    content = synthetic_blot_png()
    artifact = ArtifactReference(
        artifact_id=ARTIFACT_ID,
        sha256=hashlib.sha256(content).hexdigest(),
        media_type="image/png",
        byte_size=len(content),
    )
    lanes = (
        DensitometryLaneRegion(
            lane_id=LANE_IDS[0],
            lane_index=1,
            label="control",
            region=_region(LANE_IDS[0], 0, 0, 20, 24),
        ),
        DensitometryLaneRegion(
            lane_id=LANE_IDS[1],
            lane_index=2,
            label="treated",
            region=_region(LANE_IDS[1], 20, 0, 20, 24),
        ),
    )
    targets = (
        DensitometryTargetRegion(
            target_id=TARGET_IDS[0],
            label="p53",
            is_loading_control=False,
            region=_region(TARGET_IDS[0], 0, 0, 40, 12),
        ),
        DensitometryTargetRegion(
            target_id=TARGET_IDS[1],
            label="GAPDH",
            is_loading_control=True,
            region=_region(TARGET_IDS[1], 0, 12, 40, 12),
        ),
    )
    bands = (
        DensitometryBandRegion(
            band_id=BAND_IDS[0],
            lane_id=LANE_IDS[0],
            target_id=TARGET_IDS[0],
            region=_region(BAND_IDS[0], first_band_x, 4, 10, 4),
        ),
        DensitometryBandRegion(
            band_id=BAND_IDS[1],
            lane_id=LANE_IDS[1],
            target_id=TARGET_IDS[0],
            region=_region(BAND_IDS[1], 25, 4, 10, 4),
        ),
        DensitometryBandRegion(
            band_id=BAND_IDS[2],
            lane_id=LANE_IDS[0],
            target_id=TARGET_IDS[1],
            region=_region(BAND_IDS[2], 5, 16, 10, 4),
        ),
        DensitometryBandRegion(
            band_id=BAND_IDS[3],
            lane_id=LANE_IDS[1],
            target_id=TARGET_IDS[1],
            region=_region(BAND_IDS[3], 25, 16, 10, 4),
        ),
    )
    return DensitometryInput(
        case_id=CASE_ID,
        image_artifact=artifact,
        image_kind=(
            DensitometryImageKind.PUBLICATION_FIGURE
            if publication
            else DensitometryImageKind.RAW_SOURCE
        ),
        geometry=PredictionGeometryReference(prediction_id=PREDICTION_ID),
        lanes=lanes,
        targets=targets,
        bands=bands,
        loading_control_target_id=loading_control_target_id,
        configuration=DensitometryConfiguration(
            background_method=DensitometryBackgroundMethod.GLOBAL_PERCENTILE,
            normalization_method=DensitometryNormalizationMethod.LOADING_CONTROL,
            background_percentile=20.0,
            uneven_background_cv_threshold=10.0,
            lane_boundaries_reviewed=True,
            exposure_known=True,
        ),
    )


def synthetic_densitometry_annotation_set(
    source_artifact_id: UUID = ARTIFACT_ID,
) -> SpatialAnnotationSet:
    lanes = (
        _annotation(
            source_artifact_id,
            LANE_IDS[0],
            SpatialAnnotationType.LANE,
            0,
            0,
            20,
            24,
            "control",
        ),
        _annotation(
            source_artifact_id,
            LANE_IDS[1],
            SpatialAnnotationType.LANE,
            20,
            0,
            20,
            24,
            "treated",
        ),
    )
    targets = (
        _annotation(
            source_artifact_id,
            TARGET_IDS[0],
            SpatialAnnotationType.PROTEIN_ROW,
            0,
            0,
            40,
            12,
            "p53",
        ),
        _annotation(
            source_artifact_id,
            TARGET_IDS[1],
            SpatialAnnotationType.PROTEIN_ROW,
            0,
            12,
            40,
            12,
            "GAPDH",
        ),
    )
    bands = (
        _annotation(
            source_artifact_id,
            BAND_IDS[0],
            SpatialAnnotationType.BAND,
            5,
            4,
            10,
            4,
            "p53 lane 1",
        ),
        _annotation(
            source_artifact_id,
            BAND_IDS[1],
            SpatialAnnotationType.BAND,
            25,
            4,
            10,
            4,
            "p53 lane 2",
        ),
        _annotation(
            source_artifact_id,
            BAND_IDS[2],
            SpatialAnnotationType.BAND,
            5,
            16,
            10,
            4,
            "GAPDH lane 1",
        ),
        _annotation(
            source_artifact_id,
            BAND_IDS[3],
            SpatialAnnotationType.BAND,
            25,
            16,
            10,
            4,
            "GAPDH lane 2",
        ),
    )
    relationships = tuple(
        _relationship(parent, SpatialRelationshipType.CONTAINS, band)
        for band, lane, target in (
            (bands[0], lanes[0], targets[0]),
            (bands[1], lanes[1], targets[0]),
            (bands[2], lanes[0], targets[1]),
            (bands[3], lanes[1], targets[1]),
        )
        for parent in (lane, target)
    )
    return SpatialAnnotationSet(
        spatial_annotations=(*lanes, *targets, *bands),
        relationships=(
            *relationships,
            _relationship(
                targets[0],
                SpatialRelationshipType.TARGET_USES_LOADING_CONTROL,
                targets[1],
            ),
            _relationship(lanes[0], SpatialRelationshipType.PRECEDES, lanes[1]),
        ),
    )


def _annotation(
    source_artifact_id: UUID,
    annotation_id: UUID,
    annotation_type: SpatialAnnotationType,
    x: float,
    y: float,
    width: float,
    height: float,
    label: str,
) -> SpatialAnnotation:
    return SpatialAnnotation(
        spatial_annotation_id=annotation_id,
        annotation_type=annotation_type,
        state=ObservationState.PRESENT,
        region=_region(
            annotation_id,
            x,
            y,
            width,
            height,
            source_artifact_id=source_artifact_id,
        ),
        label=label,
    )


def _relationship(
    subject: SpatialAnnotation,
    relation_type: SpatialRelationshipType,
    object_: SpatialAnnotation,
) -> SpatialEditorRelationship:
    return SpatialEditorRelationship(
        relationship_id=uuid4(),
        subject_id=subject.spatial_annotation_id,
        relation_type=relation_type,
        object_id=object_.spatial_annotation_id,
    )


def _region(
    region_id: UUID,
    x: float,
    y: float,
    width: float,
    height: float,
    *,
    source_artifact_id: UUID = ARTIFACT_ID,
) -> BoundingRegion:
    return BoundingRegion(
        region_id=region_id,
        source_artifact_id=source_artifact_id,
        x=x,
        y=y,
        width=width,
        height=height,
        canvas_width=40,
        canvas_height=24,
    )
