from uuid import uuid4

import pytest
from hiveblot_contracts import (
    SpatialAnnotationSet,
    SpatialAnnotationType,
    SpatialEditorRelationship,
    SpatialRelationshipType,
)
from pydantic import ValidationError

from tests.fakes.spatial import spatial_annotation_set


def test_spatial_snapshot_round_trips_source_pixel_graph() -> None:
    snapshot = spatial_annotation_set(uuid4())

    restored = SpatialAnnotationSet.model_validate_json(snapshot.model_dump_json())

    assert restored == snapshot
    assert len(restored.spatial_annotations) == 9
    assert any(
        relationship.relation_type is SpatialRelationshipType.PRECEDES
        for relationship in restored.relationships
    )


def test_spatial_relationships_reject_incompatible_types_and_unknown_entities() -> None:
    snapshot = spatial_annotation_set(uuid4())
    lane = next(
        item
        for item in snapshot.spatial_annotations
        if item.annotation_type is SpatialAnnotationType.LANE
    )
    band = next(
        item
        for item in snapshot.spatial_annotations
        if item.annotation_type is SpatialAnnotationType.BAND
    )
    invalid_label = SpatialEditorRelationship(
        relationship_id=uuid4(),
        subject_id=lane.spatial_annotation_id,
        relation_type=SpatialRelationshipType.LABELS,
        object_id=band.spatial_annotation_id,
    )
    with pytest.raises(ValidationError, match="must connect a label"):
        SpatialAnnotationSet(
            spatial_annotations=snapshot.spatial_annotations,
            relationships=(invalid_label,),
        )

    unknown = invalid_label.model_copy(update={"relationship_id": uuid4(), "subject_id": uuid4()})
    with pytest.raises(ValidationError, match="must reference spatial annotations"):
        SpatialAnnotationSet(
            spatial_annotations=snapshot.spatial_annotations,
            relationships=(unknown,),
        )


def test_containment_requires_matching_coordinates_and_enclosed_geometry() -> None:
    snapshot = spatial_annotation_set(uuid4())
    figure, panel = snapshot.spatial_annotations[:2]
    smaller_figure = figure.model_copy(
        update={"region": figure.region.model_copy(update={"width": 399.0})}
    )
    outside_panel = panel.model_copy(
        update={"region": panel.region.model_copy(update={"x": 390.0, "width": 10.0})}
    )
    contains = SpatialEditorRelationship(
        relationship_id=uuid4(),
        subject_id=smaller_figure.spatial_annotation_id,
        relation_type=SpatialRelationshipType.CONTAINS,
        object_id=outside_panel.spatial_annotation_id,
    )

    with pytest.raises(ValidationError, match="fit within their parent"):
        SpatialAnnotationSet(
            spatial_annotations=(smaller_figure, outside_panel),
            relationships=(contains,),
        )


def test_spatial_relationship_edges_must_be_unique() -> None:
    snapshot = spatial_annotation_set(uuid4())
    relationship = snapshot.relationships[0]
    duplicate = relationship.model_copy(update={"relationship_id": uuid4()})

    with pytest.raises(ValidationError, match="semantically unique"):
        SpatialAnnotationSet(
            spatial_annotations=snapshot.spatial_annotations,
            relationships=(relationship, duplicate),
        )


def test_spatial_relationships_cannot_cross_source_coordinate_spaces() -> None:
    snapshot = spatial_annotation_set(uuid4())
    lanes = tuple(
        item
        for item in snapshot.spatial_annotations
        if item.annotation_type is SpatialAnnotationType.LANE
    )
    foreign_lane = lanes[1].model_copy(
        update={"region": lanes[1].region.model_copy(update={"source_artifact_id": uuid4()})}
    )
    precedes = SpatialEditorRelationship(
        relationship_id=uuid4(),
        subject_id=lanes[0].spatial_annotation_id,
        relation_type=SpatialRelationshipType.PRECEDES,
        object_id=foreign_lane.spatial_annotation_id,
    )

    with pytest.raises(ValidationError, match="same source coordinate space"):
        SpatialAnnotationSet(
            spatial_annotations=(lanes[0], foreign_lane),
            relationships=(precedes,),
        )


def test_lane_order_rejects_forks_and_cycles() -> None:
    snapshot = spatial_annotation_set(uuid4())
    lanes = tuple(
        item
        for item in snapshot.spatial_annotations
        if item.annotation_type is SpatialAnnotationType.LANE
    )
    third = lanes[1].model_copy(
        update={
            "spatial_annotation_id": uuid4(),
            "region": lanes[1].region.model_copy(
                update={"region_id": uuid4(), "x": 280.0, "width": 70.0}
            ),
        }
    )
    fork = (
        SpatialEditorRelationship(
            relationship_id=uuid4(),
            subject_id=lanes[0].spatial_annotation_id,
            relation_type=SpatialRelationshipType.PRECEDES,
            object_id=lanes[1].spatial_annotation_id,
        ),
        SpatialEditorRelationship(
            relationship_id=uuid4(),
            subject_id=lanes[0].spatial_annotation_id,
            relation_type=SpatialRelationshipType.PRECEDES,
            object_id=third.spatial_annotation_id,
        ),
    )
    with pytest.raises(ValidationError, match="at most one lane"):
        SpatialAnnotationSet(
            spatial_annotations=(*snapshot.spatial_annotations, third),
            relationships=fork,
        )
