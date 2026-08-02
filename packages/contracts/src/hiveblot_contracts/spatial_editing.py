"""Strict spatial-editing snapshots and semantic geometry comparisons."""

from __future__ import annotations

from enum import StrEnum
from typing import Self
from uuid import UUID

from pydantic import Field, StrictInt, model_validator

from .annotations import SpatialAnnotation, SpatialAnnotationType
from .base import ContractModel


class SpatialRelationshipType(StrEnum):
    CONTAINS = "contains"
    LABELS = "labels"
    GROUPED_WITH = "grouped_with"
    PRECEDES = "precedes"
    TARGET_USES_LOADING_CONTROL = "target_uses_loading_control"


class SpatialEditorRelationship(ContractModel):
    relationship_id: UUID
    subject_id: UUID
    relation_type: SpatialRelationshipType
    object_id: UUID

    @model_validator(mode="after")
    def relationship_cannot_self_reference(self) -> Self:
        if self.subject_id == self.object_id:
            raise ValueError("spatial relationship cannot self-reference")
        return self


class SpatialAnnotationSet(ContractModel):
    spatial_annotations: tuple[SpatialAnnotation, ...] = ()
    relationships: tuple[SpatialEditorRelationship, ...] = ()

    @model_validator(mode="after")
    def spatial_graph_is_valid(self) -> Self:
        annotation_ids = [item.spatial_annotation_id for item in self.spatial_annotations]
        if len(annotation_ids) != len(set(annotation_ids)):
            raise ValueError("spatial annotation IDs must be unique")
        relationship_ids = [item.relationship_id for item in self.relationships]
        if len(relationship_ids) != len(set(relationship_ids)):
            raise ValueError("spatial relationship IDs must be unique")
        relationship_edges = [
            (item.subject_id, item.relation_type, item.object_id) for item in self.relationships
        ]
        if len(relationship_edges) != len(set(relationship_edges)):
            raise ValueError("spatial relationships must be semantically unique")
        annotation_by_id = {
            annotation.spatial_annotation_id: annotation for annotation in self.spatial_annotations
        }
        for relationship in self.relationships:
            if (
                relationship.subject_id not in annotation_by_id
                or relationship.object_id not in annotation_by_id
            ):
                raise ValueError("spatial relationships must reference spatial annotations")
            _validate_relationship_types(relationship, annotation_by_id)
        _validate_directed_acyclic(
            self.relationships,
            SpatialRelationshipType.CONTAINS,
            "containment",
        )
        _validate_lane_order(self.relationships)
        return self


class GeometryDiffStatus(StrEnum):
    ADDED = "added"
    REMOVED = "removed"
    MODIFIED = "modified"
    UNCHANGED = "unchanged"


class SpatialAnnotationDelta(ContractModel):
    spatial_annotation_id: UUID
    status: GeometryDiffStatus
    prediction: SpatialAnnotation | None = None
    annotation: SpatialAnnotation | None = None
    changed_fields: tuple[str, ...] = ()


class SpatialAnnotationComparison(ContractModel):
    prediction_id: UUID
    annotation_revision_id: UUID | None = None
    deltas: tuple[SpatialAnnotationDelta, ...]
    added_count: StrictInt = Field(ge=0)
    removed_count: StrictInt = Field(ge=0)
    modified_count: StrictInt = Field(ge=0)
    unchanged_count: StrictInt = Field(ge=0)


def _validate_relationship_types(
    relationship: SpatialEditorRelationship,
    annotation_by_id: dict[UUID, SpatialAnnotation],
) -> None:
    subject = annotation_by_id[relationship.subject_id]
    object_ = annotation_by_id[relationship.object_id]
    _validate_matching_coordinate_space(subject, object_)
    if relationship.relation_type is SpatialRelationshipType.LABELS:
        if (
            subject.annotation_type is not SpatialAnnotationType.LABEL
            or object_.annotation_type is SpatialAnnotationType.LABEL
        ):
            raise ValueError("labels relationships must connect a label to a non-label annotation")
    elif relationship.relation_type is SpatialRelationshipType.PRECEDES:
        if (
            subject.annotation_type is not SpatialAnnotationType.LANE
            or object_.annotation_type is not SpatialAnnotationType.LANE
        ):
            raise ValueError("precedes relationships must connect lane annotations")
    elif relationship.relation_type is SpatialRelationshipType.TARGET_USES_LOADING_CONTROL:
        if (
            subject.annotation_type is not SpatialAnnotationType.PROTEIN_ROW
            or object_.annotation_type is not SpatialAnnotationType.PROTEIN_ROW
        ):
            raise ValueError(
                "target/loading-control relationships must connect protein-row annotations"
            )
    elif relationship.relation_type is SpatialRelationshipType.CONTAINS:
        parent_rank = _CONTAINMENT_RANK[subject.annotation_type]
        child_rank = _CONTAINMENT_RANK[object_.annotation_type]
        if parent_rank >= child_rank:
            raise ValueError("containment relationships must move from parent to child region type")
        _validate_containment_geometry(subject, object_)


_CONTAINMENT_RANK = {
    SpatialAnnotationType.FIGURE: 0,
    SpatialAnnotationType.PANEL: 1,
    SpatialAnnotationType.BLOT: 2,
    SpatialAnnotationType.LANE: 3,
    SpatialAnnotationType.PROTEIN_ROW: 3,
    SpatialAnnotationType.QUANTIFICATION_PLOT: 3,
    SpatialAnnotationType.BAND: 4,
    SpatialAnnotationType.LABEL: 4,
}


def _validate_directed_acyclic(
    relationships: tuple[SpatialEditorRelationship, ...],
    relation_type: SpatialRelationshipType,
    label: str,
) -> None:
    edges: dict[UUID, list[UUID]] = {}
    for relationship in relationships:
        if relationship.relation_type is relation_type:
            edges.setdefault(relationship.subject_id, []).append(relationship.object_id)
    visiting: set[UUID] = set()
    visited: set[UUID] = set()

    def visit(node: UUID) -> None:
        if node in visiting:
            raise ValueError(f"{label} relationships cannot contain a cycle")
        if node in visited:
            return
        visiting.add(node)
        for child in edges.get(node, ()):
            visit(child)
        visiting.remove(node)
        visited.add(node)

    for node in edges:
        visit(node)


def _validate_lane_order(
    relationships: tuple[SpatialEditorRelationship, ...],
) -> None:
    preceding: set[UUID] = set()
    following: set[UUID] = set()
    lane_relationships = tuple(
        relationship
        for relationship in relationships
        if relationship.relation_type is SpatialRelationshipType.PRECEDES
    )
    for relationship in lane_relationships:
        if relationship.subject_id in preceding:
            raise ValueError("a lane may precede at most one lane")
        if relationship.object_id in following:
            raise ValueError("a lane may follow at most one lane")
        preceding.add(relationship.subject_id)
        following.add(relationship.object_id)
    _validate_directed_acyclic(
        lane_relationships,
        SpatialRelationshipType.PRECEDES,
        "lane-order",
    )


def _validate_matching_coordinate_space(
    subject: SpatialAnnotation,
    object_: SpatialAnnotation,
) -> None:
    subject_region = subject.region
    object_region = object_.region
    if (
        subject_region.source_artifact_id != object_region.source_artifact_id
        or subject_region.page_number != object_region.page_number
        or subject_region.canvas_width != object_region.canvas_width
        or subject_region.canvas_height != object_region.canvas_height
    ):
        raise ValueError("spatial relationships must use the same source coordinate space")


def _validate_containment_geometry(
    parent: SpatialAnnotation,
    child: SpatialAnnotation,
) -> None:
    parent_region = parent.region
    child_region = child.region
    if (
        child_region.x < parent_region.x
        or child_region.y < parent_region.y
        or child_region.x + child_region.width > parent_region.x + parent_region.width
        or child_region.y + child_region.height > parent_region.y + parent_region.height
    ):
        raise ValueError("contained regions must fit within their parent geometry")
