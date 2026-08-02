"""Typed western-blot review, prediction acceptance, and semantic comparison."""

from __future__ import annotations

import json
from collections.abc import Sequence
from uuid import UUID

from hiveblot_contracts import (
    AnnotationDocumentRecord,
    AnnotationErrorCode,
    AnnotationRevision,
    AntibodyAnnotation,
    BiologicalContextAnnotation,
    CanonicalEntityReference,
    CanonicalEntityType,
    LaneConditionAnnotation,
    MolecularWeightAnnotation,
    PredictionDocument,
    ProteinAnnotation,
    ReplicateAnnotation,
    ScientificRelationship,
    SemanticAnnotationDiff,
    SemanticDiffStatus,
    StructuredAnnotationComparison,
    TreatmentAnnotation,
    WesternBlotExtractionResult,
    WesternBlotStructuredAnnotation,
)
from pydantic import ValidationError

from .errors import ConcurrencyConflict, InvalidEvaluationState
from .service import EvaluationService

type StructuredEntity = (
    ProteinAnnotation
    | BiologicalContextAnnotation
    | TreatmentAnnotation
    | LaneConditionAnnotation
    | AntibodyAnnotation
    | MolecularWeightAnnotation
    | ReplicateAnnotation
)
DEFAULT_CANONICAL_ENTITIES = (
    CanonicalEntityReference(
        entity_type=CanonicalEntityType.PROTEIN,
        canonical_id="uniprot:P04637",
        label="Cellular tumor antigen p53 (TP53)",
        vocabulary="UniProtKB",
        accession="P04637",
    ),
    CanonicalEntityReference(
        entity_type=CanonicalEntityType.PROTEIN,
        canonical_id="uniprot:P60709",
        label="Actin, cytoplasmic 1 (ACTB)",
        vocabulary="UniProtKB",
        accession="P60709",
    ),
    CanonicalEntityReference(
        entity_type=CanonicalEntityType.PROTEIN,
        canonical_id="uniprot:P04406",
        label="Glyceraldehyde-3-phosphate dehydrogenase (GAPDH)",
        vocabulary="UniProtKB",
        accession="P04406",
    ),
    CanonicalEntityReference(
        entity_type=CanonicalEntityType.CELL_LINE,
        canonical_id="cellosaurus:CVCL_0030",
        label="HeLa",
        vocabulary="Cellosaurus",
        accession="CVCL_0030",
    ),
    CanonicalEntityReference(
        entity_type=CanonicalEntityType.CELL_LINE,
        canonical_id="cellosaurus:CVCL_0045",
        label="HEK293",
        vocabulary="Cellosaurus",
        accession="CVCL_0045",
    ),
    CanonicalEntityReference(
        entity_type=CanonicalEntityType.TISSUE,
        canonical_id="uberon:UBERON_0002107",
        label="Liver",
        vocabulary="UBERON",
        accession="UBERON_0002107",
    ),
    CanonicalEntityReference(
        entity_type=CanonicalEntityType.ORGANISM,
        canonical_id="ncbitaxon:9606",
        label="Homo sapiens",
        vocabulary="NCBI Taxonomy",
        accession="9606",
    ),
    CanonicalEntityReference(
        entity_type=CanonicalEntityType.ORGANISM,
        canonical_id="ncbitaxon:10090",
        label="Mus musculus",
        vocabulary="NCBI Taxonomy",
        accession="10090",
    ),
    CanonicalEntityReference(
        entity_type=CanonicalEntityType.TREATMENT,
        canonical_id="chebi:CHEBI_52217",
        label="Epidermal growth factor",
        vocabulary="ChEBI",
        accession="CHEBI_52217",
    ),
)


class CanonicalEntityCatalog:
    def __init__(
        self,
        entities: Sequence[CanonicalEntityReference] = DEFAULT_CANONICAL_ENTITIES,
    ) -> None:
        self._entities = tuple(entities)

    def lookup(
        self,
        *,
        query: str,
        entity_type: CanonicalEntityType | None,
        limit: int,
    ) -> tuple[CanonicalEntityReference, ...]:
        normalized = query.strip().casefold()
        if not normalized:
            return ()
        matches = (
            entity
            for entity in self._entities
            if (entity_type is None or entity.entity_type is entity_type)
            and normalized
            in " ".join(
                (
                    entity.canonical_id,
                    entity.label,
                    entity.vocabulary,
                    entity.accession or "",
                )
            ).casefold()
        )
        return tuple(matches)[:limit]


class StructuredAnnotationService:
    def __init__(
        self,
        evaluation: EvaluationService,
        catalog: CanonicalEntityCatalog | None = None,
    ) -> None:
        self._evaluation = evaluation
        self._catalog = catalog or CanonicalEntityCatalog()

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

    def save(
        self,
        case_id: UUID,
        *,
        reviewer_id: UUID,
        expected_head_revision_id: UUID | None,
        annotation: WesternBlotStructuredAnnotation,
        rationale: str | None,
        error_codes: tuple[str, ...],
    ) -> tuple[AnnotationDocumentRecord, AnnotationRevision]:
        current = self.reviewer_annotation(case_id, reviewer_id)
        if current is None:
            if expected_head_revision_id is not None:
                raise ConcurrencyConflict("annotation does not have an existing head")
            return self._evaluation.create_annotation(
                case_id,
                reviewer_id=reviewer_id,
                rationale=rationale,
                error_codes=error_codes,
                field_annotations=(),
                spatial_annotations=(),
                relationships=(),
                structured_annotation=annotation,
            )
        document, head = current
        if expected_head_revision_id is None:
            raise ConcurrencyConflict("annotation already exists; provide its expected head")
        return self._evaluation.append_revision(
            document.annotation_id,
            expected_head_revision_id=expected_head_revision_id,
            reviewer_id=reviewer_id,
            rationale=rationale,
            error_codes=error_codes,
            field_annotations=head.field_annotations,
            spatial_annotations=head.spatial_annotations,
            relationships=head.relationships,
            structured_annotation=annotation,
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
        self._evaluation.get_annotation(annotation_id)
        target = self._evaluation.get_revision(target_revision_id)
        if target.annotation_id != annotation_id:
            raise InvalidEvaluationState("undo target must belong to the annotation document")
        return self._evaluation.append_revision(
            annotation_id,
            expected_head_revision_id=expected_head_revision_id,
            reviewer_id=reviewer_id,
            rationale=rationale or f"Restored revision {target.revision_number}",
            error_codes=target.error_codes,
            field_annotations=target.field_annotations,
            spatial_annotations=target.spatial_annotations,
            relationships=target.relationships,
            structured_annotation=target.structured_annotation,
        )

    def prediction_annotation(
        self,
        case_id: UUID,
        prediction_id: UUID,
    ) -> tuple[PredictionDocument, WesternBlotStructuredAnnotation]:
        prediction = self._evaluation.get_prediction(prediction_id)
        if prediction.case_id != case_id:
            raise InvalidEvaluationState("prediction must belong to the evaluation case")
        if prediction.normalized_output_json is None:
            raise InvalidEvaluationState("prediction has no normalized structured output")
        try:
            if prediction.prediction_schema == "western-blot-extraction-result":
                annotation = WesternBlotExtractionResult.model_validate_json(
                    prediction.normalized_output_json
                ).structured_annotation
            else:
                annotation = WesternBlotStructuredAnnotation.model_validate_json(
                    prediction.normalized_output_json
                )
        except ValidationError as exc:
            raise InvalidEvaluationState(
                "prediction normalized output is not a western-blot structured annotation"
            ) from exc
        return prediction, annotation

    def accept_prediction(
        self,
        case_id: UUID,
        *,
        prediction_id: UUID,
        base_annotation: WesternBlotStructuredAnnotation | None,
        accepted_entity_ids: tuple[UUID, ...],
        accept_all: bool,
    ) -> WesternBlotStructuredAnnotation:
        _, predicted = self.prediction_annotation(case_id, prediction_id)
        predicted_ids = {entity.entity_id for entity in _entities(predicted)}
        selected = predicted_ids if accept_all else set(accepted_entity_ids)
        unknown = selected - predicted_ids
        if unknown:
            raise InvalidEvaluationState("accepted entity IDs must belong to the prediction")
        if not selected:
            raise InvalidEvaluationState("select at least one predicted entity")
        base = base_annotation or WesternBlotStructuredAnnotation()
        relationships = _merge_relationships(base, predicted, selected, accept_all=accept_all)
        payload = base.model_copy(
            update={
                "proteins": _merge_entities(base.proteins, predicted.proteins, selected),
                "biological_contexts": _merge_entities(
                    base.biological_contexts,
                    predicted.biological_contexts,
                    selected,
                ),
                "treatments": _merge_entities(base.treatments, predicted.treatments, selected),
                "lane_conditions": _merge_entities(
                    base.lane_conditions,
                    predicted.lane_conditions,
                    selected,
                ),
                "antibodies": _merge_entities(base.antibodies, predicted.antibodies, selected),
                "molecular_weights": _merge_entities(
                    base.molecular_weights,
                    predicted.molecular_weights,
                    selected,
                ),
                "replicates": _merge_entities(base.replicates, predicted.replicates, selected),
                "relationships": relationships,
            }
        )
        return WesternBlotStructuredAnnotation.model_validate(payload.model_dump(mode="python"))

    def compare(
        self,
        *,
        prediction_id: UUID,
        prediction: WesternBlotStructuredAnnotation,
        annotation_revision_id: UUID | None,
        annotation: WesternBlotStructuredAnnotation,
    ) -> StructuredAnnotationComparison:
        predicted_fields = _flatten_annotation(prediction)
        annotated_fields = _flatten_annotation(annotation)
        differences = []
        counts = {status: 0 for status in SemanticDiffStatus}
        for field_path in sorted(predicted_fields.keys() | annotated_fields.keys()):
            predicted = predicted_fields.get(field_path, _MISSING)
            annotated = annotated_fields.get(field_path, _MISSING)
            if predicted is _MISSING:
                status = SemanticDiffStatus.ADDED
            elif annotated is _MISSING:
                status = SemanticDiffStatus.REMOVED
            elif predicted == annotated:
                status = SemanticDiffStatus.UNCHANGED
            else:
                status = SemanticDiffStatus.MODIFIED
            counts[status] += 1
            differences.append(
                SemanticAnnotationDiff(
                    field_path=field_path,
                    status=status,
                    prediction_value_json=(
                        None if predicted is _MISSING else _json_value(predicted)
                    ),
                    annotation_value_json=(
                        None if annotated is _MISSING else _json_value(annotated)
                    ),
                )
            )
        return StructuredAnnotationComparison(
            prediction_id=prediction_id,
            annotation_revision_id=annotation_revision_id,
            differences=tuple(differences),
            added_count=counts[SemanticDiffStatus.ADDED],
            removed_count=counts[SemanticDiffStatus.REMOVED],
            modified_count=counts[SemanticDiffStatus.MODIFIED],
            unchanged_count=counts[SemanticDiffStatus.UNCHANGED],
        )

    def lookup_entities(
        self,
        *,
        query: str,
        entity_type: CanonicalEntityType | None,
        limit: int,
    ) -> tuple[CanonicalEntityReference, ...]:
        return self._catalog.lookup(query=query, entity_type=entity_type, limit=limit)


def _entities(annotation: WesternBlotStructuredAnnotation) -> tuple[StructuredEntity, ...]:
    return (
        *annotation.proteins,
        *annotation.biological_contexts,
        *annotation.treatments,
        *annotation.lane_conditions,
        *annotation.antibodies,
        *annotation.molecular_weights,
        *annotation.replicates,
    )


def _merge_entities[EntityT: StructuredEntity](
    base: tuple[EntityT, ...],
    predicted: tuple[EntityT, ...],
    selected: set[UUID],
) -> tuple[EntityT, ...]:
    replacements = {
        entity.entity_id: entity for entity in predicted if entity.entity_id in selected
    }
    merged = [replacements.pop(entity.entity_id, entity) for entity in base]
    merged.extend(entity for entity in predicted if entity.entity_id in replacements)
    return tuple(merged)


def _merge_relationships(
    base: WesternBlotStructuredAnnotation,
    predicted: WesternBlotStructuredAnnotation,
    selected: set[UUID],
    *,
    accept_all: bool,
) -> tuple[ScientificRelationship, ...]:
    result_ids = {entity.entity_id for entity in _entities(base)} | selected
    accepted = {
        relationship.relationship_id: relationship
        for relationship in predicted.relationships
        if relationship.subject_id in result_ids
        and relationship.object_id in result_ids
        and (
            accept_all or relationship.subject_id in selected or relationship.object_id in selected
        )
    }
    merged = [accepted.pop(item.relationship_id, item) for item in base.relationships]
    merged.extend(
        relationship
        for relationship in predicted.relationships
        if relationship.relationship_id in accepted
    )
    return tuple(merged)


_MISSING = object()


def _flatten_annotation(annotation: WesternBlotStructuredAnnotation) -> dict[str, object]:
    flattened: dict[str, object] = {}

    def visit(value: object, path: str) -> None:
        if isinstance(value, dict):
            for key, child in sorted(value.items()):
                if key == "schema_version":
                    continue
                visit(child, f"{path}/{key}")
            return
        if isinstance(value, list):
            keyed = all(
                isinstance(item, dict) and ("entity_id" in item or "relationship_id" in item)
                for item in value
            )
            for index, child in enumerate(value):
                if keyed:
                    assert isinstance(child, dict)
                    identity = child.get("entity_id", child.get("relationship_id"))
                else:
                    identity = index
                visit(child, f"{path}/{identity}")
            return
        flattened[path or "/"] = value

    visit(annotation.model_dump(mode="json"), "")
    return flattened


def _json_value(value: object) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":"))
