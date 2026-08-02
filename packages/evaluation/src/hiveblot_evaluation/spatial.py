"""Spatial review snapshots, prediction acceptance, and geometry comparison."""

from __future__ import annotations

from collections.abc import Sequence
from uuid import UUID

from hiveblot_contracts import (
    AnnotationDocumentRecord,
    AnnotationErrorCode,
    AnnotationRelationship,
    AnnotationRevision,
    GeometryDiffStatus,
    ObservationState,
    PredictionDocument,
    PredictionEvidence,
    SpatialAnnotation,
    SpatialAnnotationComparison,
    SpatialAnnotationDelta,
    SpatialAnnotationSet,
    SpatialAnnotationType,
    SpatialEditorRelationship,
    SpatialRelationshipType,
)
from pydantic import ValidationError

from .errors import ConcurrencyConflict, InvalidEvaluationState
from .service import EvaluationService

SPATIAL_RELATIONSHIP_PREFIX = "spatial:"
PERSISTED_SPATIAL_RELATIONSHIPS = {
    f"{SPATIAL_RELATIONSHIP_PREFIX}{item.value}": item for item in SpatialRelationshipType
}


class SpatialAnnotationService:
    def __init__(self, evaluation: EvaluationService) -> None:
        self._evaluation = evaluation

    def reviewer_annotation(
        self,
        case_id: UUID,
        reviewer_id: UUID,
    ) -> tuple[AnnotationDocumentRecord, AnnotationRevision] | None:
        self._evaluation.get_case(case_id)
        document = next(
            (
                item
                for item in self._evaluation.list_annotations(case_id)
                if item.reviewer_id == reviewer_id
            ),
            None,
        )
        if document is None:
            return None
        return document, self._evaluation.get_revision(document.head_revision_id)

    def list_predictions(self, case_id: UUID) -> Sequence[PredictionDocument]:
        return self._evaluation.list_predictions(case_id)

    def list_revisions(self, annotation_id: UUID) -> Sequence[AnnotationRevision]:
        return self._evaluation.list_revisions(annotation_id)

    def list_error_codes(self) -> Sequence[AnnotationErrorCode]:
        return self._evaluation.list_error_codes()

    def annotation_set(self, revision: AnnotationRevision | None) -> SpatialAnnotationSet:
        if revision is None:
            return SpatialAnnotationSet()
        spatial_ids = {
            annotation.spatial_annotation_id for annotation in revision.spatial_annotations
        }
        return _validated_annotation_set(
            {
                "spatial_annotations": revision.spatial_annotations,
                "relationships": tuple(
                    SpatialEditorRelationship(
                        relationship_id=relationship.relationship_id,
                        subject_id=relationship.subject_id,
                        relation_type=PERSISTED_SPATIAL_RELATIONSHIPS[relationship.relation_type],
                        object_id=relationship.object_id,
                    )
                    for relationship in revision.relationships
                    if _is_spatial_relationship(relationship, spatial_ids)
                ),
            },
            "stored spatial annotation graph is invalid",
        )

    def save(
        self,
        case_id: UUID,
        *,
        reviewer_id: UUID,
        expected_head_revision_id: UUID | None,
        annotation_set: SpatialAnnotationSet,
        rationale: str | None,
        error_codes: tuple[str, ...],
    ) -> tuple[AnnotationDocumentRecord, AnnotationRevision]:
        annotation_set = _validated_annotation_set(
            annotation_set.model_dump(mode="python"),
            "spatial annotation graph is invalid",
        )
        current = self.reviewer_annotation(case_id, reviewer_id)
        spatial_relationships = _persistence_relationships(annotation_set)
        if current is None:
            if expected_head_revision_id is not None:
                raise ConcurrencyConflict("annotation does not have an existing head")
            return self._evaluation.create_annotation(
                case_id,
                reviewer_id=reviewer_id,
                rationale=rationale,
                error_codes=error_codes,
                field_annotations=(),
                spatial_annotations=annotation_set.spatial_annotations,
                relationships=spatial_relationships,
            )
        document, head = current
        if expected_head_revision_id is None:
            raise ConcurrencyConflict("annotation already exists; provide its expected head")
        head_spatial_ids = {
            annotation.spatial_annotation_id for annotation in head.spatial_annotations
        }
        preserved_relationships = tuple(
            relationship
            for relationship in head.relationships
            if not _is_spatial_relationship(relationship, head_spatial_ids)
        )
        _validate_relationship_id_partition(preserved_relationships, spatial_relationships)
        return self._evaluation.append_revision(
            document.annotation_id,
            expected_head_revision_id=expected_head_revision_id,
            reviewer_id=reviewer_id,
            rationale=rationale,
            error_codes=error_codes,
            field_annotations=head.field_annotations,
            spatial_annotations=annotation_set.spatial_annotations,
            relationships=(*preserved_relationships, *spatial_relationships),
            structured_annotation=head.structured_annotation,
        )

    def undo(
        self,
        annotation_id: UUID,
        *,
        reviewer_id: UUID,
        expected_head_revision_id: UUID,
        target_revision_id: UUID,
        rationale: str | None,
    ) -> tuple[AnnotationDocumentRecord, AnnotationRevision]:
        document = self._evaluation.get_annotation(annotation_id)
        head = self._evaluation.get_revision(document.head_revision_id)
        target = self._evaluation.get_revision(target_revision_id)
        if target.annotation_id != annotation_id:
            raise InvalidEvaluationState("undo target must belong to the annotation document")
        target_set = self.annotation_set(target)
        spatial_relationships = _persistence_relationships(target_set)
        head_spatial_ids = {
            annotation.spatial_annotation_id for annotation in head.spatial_annotations
        }
        preserved_relationships = tuple(
            relationship
            for relationship in head.relationships
            if not _is_spatial_relationship(relationship, head_spatial_ids)
        )
        _validate_relationship_id_partition(preserved_relationships, spatial_relationships)
        return self._evaluation.append_revision(
            annotation_id,
            expected_head_revision_id=expected_head_revision_id,
            reviewer_id=reviewer_id,
            rationale=rationale or f"Restored spatial revision {target.revision_number}",
            error_codes=head.error_codes,
            field_annotations=head.field_annotations,
            spatial_annotations=target.spatial_annotations,
            relationships=(*preserved_relationships, *spatial_relationships),
            structured_annotation=head.structured_annotation,
        )

    def prediction_set(
        self,
        case_id: UUID,
        prediction_id: UUID,
    ) -> tuple[PredictionDocument, SpatialAnnotationSet]:
        prediction = self._evaluation.get_prediction(prediction_id)
        if prediction.case_id != case_id:
            raise InvalidEvaluationState("prediction must belong to the evaluation case")
        annotations: dict[UUID, SpatialAnnotation] = {}
        for evidence in prediction.evidence:
            if evidence.region is None:
                continue
            spatial_id = evidence.region.region_id
            annotations.setdefault(
                spatial_id,
                SpatialAnnotation(
                    spatial_annotation_id=spatial_id,
                    annotation_type=_infer_annotation_type(evidence),
                    state=ObservationState.PRESENT,
                    region=evidence.region,
                    label=evidence.description or evidence.field_path,
                ),
            )
        if not annotations:
            raise InvalidEvaluationState("prediction has no drawable source-pixel regions")
        return prediction, SpatialAnnotationSet(spatial_annotations=tuple(annotations.values()))

    def accept_prediction(
        self,
        case_id: UUID,
        *,
        prediction_id: UUID,
        base: SpatialAnnotationSet | None,
        accepted_region_ids: tuple[UUID, ...],
        accept_all: bool,
    ) -> SpatialAnnotationSet:
        _, predicted = self.prediction_set(case_id, prediction_id)
        predicted_by_id = {
            item.spatial_annotation_id: item for item in predicted.spatial_annotations
        }
        selected = set(predicted_by_id) if accept_all else set(accepted_region_ids)
        unknown = selected - predicted_by_id.keys()
        if unknown:
            raise InvalidEvaluationState("accepted region IDs must belong to the prediction")
        if not selected:
            raise InvalidEvaluationState("select at least one predicted region")
        current = base or SpatialAnnotationSet()
        replacements = {
            region_id: annotation
            for region_id, annotation in predicted_by_id.items()
            if region_id in selected
        }
        merged = [
            replacements.pop(annotation.spatial_annotation_id, annotation)
            for annotation in current.spatial_annotations
        ]
        merged.extend(
            annotation
            for annotation in predicted.spatial_annotations
            if annotation.spatial_annotation_id in replacements
        )
        return _validated_annotation_set(
            {
                "spatial_annotations": tuple(merged),
                "relationships": current.relationships,
            },
            "accepted prediction conflicts with current spatial relationships",
        )

    def compare(
        self,
        *,
        prediction_id: UUID,
        prediction: SpatialAnnotationSet,
        annotation_revision_id: UUID | None,
        annotation: SpatialAnnotationSet,
    ) -> SpatialAnnotationComparison:
        predicted = {item.spatial_annotation_id: item for item in prediction.spatial_annotations}
        reviewed = {item.spatial_annotation_id: item for item in annotation.spatial_annotations}
        deltas = []
        counts = {status: 0 for status in GeometryDiffStatus}
        for spatial_id in sorted(predicted.keys() | reviewed.keys(), key=str):
            before = predicted.get(spatial_id)
            after = reviewed.get(spatial_id)
            changed_fields: tuple[str, ...] = ()
            if before is None:
                status = GeometryDiffStatus.ADDED
            elif after is None:
                status = GeometryDiffStatus.REMOVED
            else:
                changed_fields = _changed_fields(before, after)
                status = (
                    GeometryDiffStatus.MODIFIED if changed_fields else GeometryDiffStatus.UNCHANGED
                )
            counts[status] += 1
            deltas.append(
                SpatialAnnotationDelta(
                    spatial_annotation_id=spatial_id,
                    status=status,
                    prediction=before,
                    annotation=after,
                    changed_fields=changed_fields,
                )
            )
        return SpatialAnnotationComparison(
            prediction_id=prediction_id,
            annotation_revision_id=annotation_revision_id,
            deltas=tuple(deltas),
            added_count=counts[GeometryDiffStatus.ADDED],
            removed_count=counts[GeometryDiffStatus.REMOVED],
            modified_count=counts[GeometryDiffStatus.MODIFIED],
            unchanged_count=counts[GeometryDiffStatus.UNCHANGED],
        )


def _persistence_relationships(
    annotation_set: SpatialAnnotationSet,
) -> tuple[AnnotationRelationship, ...]:
    return tuple(
        AnnotationRelationship(
            relationship_id=relationship.relationship_id,
            subject_id=relationship.subject_id,
            relation_type=f"{SPATIAL_RELATIONSHIP_PREFIX}{relationship.relation_type.value}",
            object_id=relationship.object_id,
        )
        for relationship in annotation_set.relationships
    )


def _validated_annotation_set(value: object, message: str) -> SpatialAnnotationSet:
    try:
        return SpatialAnnotationSet.model_validate(value)
    except ValidationError as exc:
        raise InvalidEvaluationState(message) from exc


def _validate_relationship_id_partition(
    preserved_relationships: tuple[AnnotationRelationship, ...],
    spatial_relationships: tuple[AnnotationRelationship, ...],
) -> None:
    preserved_ids = {relationship.relationship_id for relationship in preserved_relationships}
    spatial_ids = {relationship.relationship_id for relationship in spatial_relationships}
    if preserved_ids & spatial_ids:
        raise InvalidEvaluationState(
            "spatial relationship IDs must not collide with preserved annotation relationships"
        )


def _is_spatial_relationship(
    relationship: AnnotationRelationship,
    spatial_ids: set[UUID],
) -> bool:
    return (
        relationship.relation_type in PERSISTED_SPATIAL_RELATIONSHIPS
        and relationship.subject_id in spatial_ids
        and relationship.object_id in spatial_ids
    )


def _infer_annotation_type(evidence: PredictionEvidence) -> SpatialAnnotationType:
    text = " ".join(
        value for value in (evidence.field_path, evidence.description) if value is not None
    ).casefold()
    patterns = (
        ("quant", SpatialAnnotationType.QUANTIFICATION_PLOT),
        ("loading control", SpatialAnnotationType.PROTEIN_ROW),
        ("protein row", SpatialAnnotationType.PROTEIN_ROW),
        ("protein_row", SpatialAnnotationType.PROTEIN_ROW),
        ("target", SpatialAnnotationType.PROTEIN_ROW),
        ("lane", SpatialAnnotationType.LANE),
        ("band", SpatialAnnotationType.BAND),
        ("label", SpatialAnnotationType.LABEL),
        ("blot", SpatialAnnotationType.BLOT),
        ("panel", SpatialAnnotationType.PANEL),
        ("figure", SpatialAnnotationType.FIGURE),
    )
    return next(
        (annotation_type for token, annotation_type in patterns if token in text),
        SpatialAnnotationType.BAND,
    )


def _changed_fields(
    prediction: SpatialAnnotation,
    annotation: SpatialAnnotation,
) -> tuple[str, ...]:
    changed = []
    if prediction.annotation_type is not annotation.annotation_type:
        changed.append("annotation_type")
    if prediction.state is not annotation.state:
        changed.append("state")
    if prediction.label != annotation.label:
        changed.append("label")
    for field_name in (
        "source_artifact_id",
        "x",
        "y",
        "width",
        "height",
        "canvas_width",
        "canvas_height",
        "page_number",
    ):
        if getattr(prediction.region, field_name) != getattr(annotation.region, field_name):
            changed.append(f"region.{field_name}")
    return tuple(changed)
