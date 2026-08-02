"""Builders for source-pixel spatial annotation snapshots."""

from uuid import UUID, uuid4

from hiveblot_contracts import (
    BoundingRegion,
    ObservationState,
    SpatialAnnotation,
    SpatialAnnotationSet,
    SpatialAnnotationType,
    SpatialEditorRelationship,
    SpatialRelationshipType,
)


def spatial_annotation_set(source_artifact_id: UUID) -> SpatialAnnotationSet:
    figure = _annotation(source_artifact_id, SpatialAnnotationType.FIGURE, 0, 0, 400, 200)
    panel = _annotation(source_artifact_id, SpatialAnnotationType.PANEL, 10, 10, 380, 180)
    blot = _annotation(source_artifact_id, SpatialAnnotationType.BLOT, 40, 45, 300, 100)
    lane_one = _annotation(source_artifact_id, SpatialAnnotationType.LANE, 70, 50, 80, 90, "Lane 1")
    lane_two = _annotation(
        source_artifact_id, SpatialAnnotationType.LANE, 180, 50, 80, 90, "Lane 2"
    )
    target = _annotation(
        source_artifact_id,
        SpatialAnnotationType.PROTEIN_ROW,
        45,
        60,
        285,
        25,
        "TP53",
    )
    control = _annotation(
        source_artifact_id,
        SpatialAnnotationType.PROTEIN_ROW,
        45,
        105,
        285,
        25,
        "ACTB",
    )
    band = _annotation(source_artifact_id, SpatialAnnotationType.BAND, 88, 64, 34, 12, "TP53 band")
    label = _annotation(source_artifact_id, SpatialAnnotationType.LABEL, 5, 58, 32, 16, "TP53")
    annotations = (figure, panel, blot, lane_one, lane_two, target, control, band, label)
    relationships = (
        _relationship(figure, SpatialRelationshipType.CONTAINS, panel),
        _relationship(panel, SpatialRelationshipType.CONTAINS, blot),
        _relationship(blot, SpatialRelationshipType.CONTAINS, lane_one),
        _relationship(blot, SpatialRelationshipType.CONTAINS, lane_two),
        _relationship(blot, SpatialRelationshipType.CONTAINS, target),
        _relationship(blot, SpatialRelationshipType.CONTAINS, control),
        _relationship(lane_one, SpatialRelationshipType.CONTAINS, band),
        _relationship(label, SpatialRelationshipType.LABELS, band),
        _relationship(lane_one, SpatialRelationshipType.PRECEDES, lane_two),
        _relationship(target, SpatialRelationshipType.TARGET_USES_LOADING_CONTROL, control),
    )
    return SpatialAnnotationSet(
        spatial_annotations=annotations,
        relationships=relationships,
    )


def _annotation(
    source_artifact_id: UUID,
    annotation_type: SpatialAnnotationType,
    x: float,
    y: float,
    width: float,
    height: float,
    label: str | None = None,
) -> SpatialAnnotation:
    annotation_id = uuid4()
    return SpatialAnnotation(
        spatial_annotation_id=annotation_id,
        annotation_type=annotation_type,
        state=ObservationState.PRESENT,
        region=BoundingRegion(
            region_id=annotation_id,
            source_artifact_id=source_artifact_id,
            x=x,
            y=y,
            width=width,
            height=height,
            canvas_width=400,
            canvas_height=200,
            page_number=1,
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
