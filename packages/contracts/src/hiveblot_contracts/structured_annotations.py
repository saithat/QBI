"""Typed western-blot scientific annotation contracts."""

from __future__ import annotations

from collections.abc import Mapping
from enum import StrEnum
from typing import Literal, Self
from uuid import UUID

from pydantic import Field, StrictFloat, StrictInt, model_validator

from .base import ContractModel, Identifier
from .observation import ObservationState


class CanonicalEntityType(StrEnum):
    PROTEIN = "protein"
    CELL_LINE = "cell_line"
    TISSUE = "tissue"
    ORGANISM = "organism"
    TREATMENT = "treatment"
    GENOTYPE = "genotype"
    ANTIBODY = "antibody"


class CanonicalEntityReference(ContractModel):
    entity_type: CanonicalEntityType
    canonical_id: Identifier
    label: str = Field(min_length=1, max_length=1000)
    vocabulary: Identifier
    accession: str | None = Field(default=None, min_length=1, max_length=500)


class ProteinRole(StrEnum):
    TARGET = "target"
    LOADING_CONTROL = "loading_control"


class BiologicalContextType(StrEnum):
    CELL_LINE = "cell_line"
    TISSUE = "tissue"
    ORGANISM = "organism"
    GENOTYPE = "genotype"


class ScientificRelationshipType(StrEnum):
    LANE_CONTAINS_PROTEIN = "lane_contains_protein"
    LANE_RECEIVES_TREATMENT = "lane_receives_treatment"
    LANE_USES_BIOLOGICAL_CONTEXT = "lane_uses_biological_context"
    LANE_HAS_REPLICATE = "lane_has_replicate"
    PROTEIN_USES_LOADING_CONTROL = "protein_uses_loading_control"
    PROTEIN_DETECTED_BY_ANTIBODY = "protein_detected_by_antibody"
    PROTEIN_HAS_MOLECULAR_WEIGHT = "protein_has_molecular_weight"


class Quantity(ContractModel):
    value: StrictFloat = Field(gt=0)
    unit: Identifier


class ScientificEntityBase(ContractModel):
    entity_id: UUID
    state: ObservationState = ObservationState.PRESENT
    original_extracted_text: str | None = Field(default=None, max_length=10_000)
    evidence_region_ids: tuple[UUID, ...] = ()
    notes: str | None = Field(default=None, max_length=10_000)


class ProteinAnnotation(ScientificEntityBase):
    entity_type: Literal["protein"] = "protein"
    role: ProteinRole
    name: str | None = Field(default=None, min_length=1, max_length=1000)
    canonical_reference: CanonicalEntityReference | None = None

    @model_validator(mode="after")
    def present_protein_requires_name(self) -> Self:
        if self.state is ObservationState.PRESENT and self.name is None:
            raise ValueError("present protein annotations require a name")
        if (
            self.canonical_reference is not None
            and self.canonical_reference.entity_type is not CanonicalEntityType.PROTEIN
        ):
            raise ValueError("protein canonical reference must have protein entity type")
        return self


class BiologicalContextAnnotation(ScientificEntityBase):
    entity_type: Literal["biological_context"] = "biological_context"
    context_type: BiologicalContextType
    name: str | None = Field(default=None, min_length=1, max_length=1000)
    canonical_reference: CanonicalEntityReference | None = None

    @model_validator(mode="after")
    def biological_context_is_consistent(self) -> Self:
        if self.state is ObservationState.PRESENT and self.name is None:
            raise ValueError("present biological context annotations require a name")
        if self.canonical_reference is not None:
            expected = CanonicalEntityType(self.context_type.value)
            if self.canonical_reference.entity_type is not expected:
                raise ValueError("biological context canonical reference has the wrong entity type")
        return self


class TreatmentAnnotation(ScientificEntityBase):
    entity_type: Literal["treatment"] = "treatment"
    name: str | None = Field(default=None, min_length=1, max_length=1000)
    dose: Quantity | None = None
    duration: Quantity | None = None
    canonical_reference: CanonicalEntityReference | None = None

    @model_validator(mode="after")
    def treatment_is_consistent(self) -> Self:
        if self.state is ObservationState.PRESENT and self.name is None:
            raise ValueError("present treatment annotations require a name")
        if (
            self.canonical_reference is not None
            and self.canonical_reference.entity_type is not CanonicalEntityType.TREATMENT
        ):
            raise ValueError("treatment canonical reference must have treatment entity type")
        return self


class LaneConditionAnnotation(ScientificEntityBase):
    entity_type: Literal["lane_condition"] = "lane_condition"
    lane_index: StrictInt = Field(ge=1)
    lane_label: str | None = Field(default=None, min_length=1, max_length=1000)
    condition_label: str | None = Field(default=None, min_length=1, max_length=2000)


class AntibodyAnnotation(ScientificEntityBase):
    entity_type: Literal["antibody"] = "antibody"
    name: str | None = Field(default=None, min_length=1, max_length=1000)
    vendor: str | None = Field(default=None, min_length=1, max_length=1000)
    catalog_number: str | None = Field(default=None, min_length=1, max_length=500)
    clone: str | None = Field(default=None, min_length=1, max_length=500)
    host_species: str | None = Field(default=None, min_length=1, max_length=500)
    dilution: str | None = Field(default=None, min_length=1, max_length=500)
    canonical_reference: CanonicalEntityReference | None = None

    @model_validator(mode="after")
    def antibody_is_consistent(self) -> Self:
        if self.state is ObservationState.PRESENT and not any(
            (self.name, self.vendor, self.catalog_number, self.clone)
        ):
            raise ValueError("present antibody annotations require identifying information")
        if (
            self.canonical_reference is not None
            and self.canonical_reference.entity_type is not CanonicalEntityType.ANTIBODY
        ):
            raise ValueError("antibody canonical reference must have antibody entity type")
        return self


class MolecularWeightAnnotation(ScientificEntityBase):
    entity_type: Literal["molecular_weight"] = "molecular_weight"
    value_kda: StrictFloat | None = Field(default=None, gt=0)

    @model_validator(mode="after")
    def present_weight_requires_value(self) -> Self:
        if self.state is ObservationState.PRESENT and self.value_kda is None:
            raise ValueError("present molecular-weight annotations require value_kda")
        return self


class ReplicateAnnotation(ScientificEntityBase):
    entity_type: Literal["replicate"] = "replicate"
    biological_replicates: StrictInt | None = Field(default=None, ge=1)
    technical_replicates: StrictInt | None = Field(default=None, ge=1)
    description: str | None = Field(default=None, min_length=1, max_length=2000)

    @model_validator(mode="after")
    def present_replicate_requires_detail(self) -> Self:
        if self.state is ObservationState.PRESENT and not any(
            (
                self.biological_replicates is not None,
                self.technical_replicates is not None,
                self.description is not None,
            )
        ):
            raise ValueError("present replicate annotations require counts or a description")
        return self


class ScientificRelationship(ContractModel):
    relationship_id: UUID
    subject_id: UUID
    relation_type: ScientificRelationshipType
    object_id: UUID

    @model_validator(mode="after")
    def relationship_cannot_self_reference(self) -> Self:
        if self.subject_id == self.object_id:
            raise ValueError("scientific relationship cannot self-reference")
        return self


class WesternBlotStructuredAnnotation(ContractModel):
    assay_type: Literal["western_blot"] = "western_blot"
    proteins: tuple[ProteinAnnotation, ...] = ()
    biological_contexts: tuple[BiologicalContextAnnotation, ...] = ()
    treatments: tuple[TreatmentAnnotation, ...] = ()
    lane_conditions: tuple[LaneConditionAnnotation, ...] = ()
    antibodies: tuple[AntibodyAnnotation, ...] = ()
    molecular_weights: tuple[MolecularWeightAnnotation, ...] = ()
    replicates: tuple[ReplicateAnnotation, ...] = ()
    relationships: tuple[ScientificRelationship, ...] = ()
    reviewer_notes: str | None = Field(default=None, max_length=20_000)

    @model_validator(mode="after")
    def entity_graph_is_valid(self) -> Self:
        collections = (
            self.proteins,
            self.biological_contexts,
            self.treatments,
            self.lane_conditions,
            self.antibodies,
            self.molecular_weights,
            self.replicates,
        )
        entities = [entity for collection in collections for entity in collection]
        entity_ids = [entity.entity_id for entity in entities]
        if len(entity_ids) != len(set(entity_ids)):
            raise ValueError("structured annotation entity IDs must be globally unique")
        relationship_ids = [relationship.relationship_id for relationship in self.relationships]
        if len(relationship_ids) != len(set(relationship_ids)):
            raise ValueError("structured annotation relationship IDs must be unique")
        entity_by_id = {entity.entity_id: entity for entity in entities}
        for relationship in self.relationships:
            if (
                relationship.subject_id not in entity_by_id
                or relationship.object_id not in entity_by_id
            ):
                raise ValueError("scientific relationships must reference structured entities")
            _validate_relationship_types(relationship, entity_by_id)
        return self


class SemanticDiffStatus(StrEnum):
    ADDED = "added"
    REMOVED = "removed"
    MODIFIED = "modified"
    UNCHANGED = "unchanged"


class SemanticAnnotationDiff(ContractModel):
    field_path: str = Field(pattern=r"^/", max_length=2000)
    status: SemanticDiffStatus
    prediction_value_json: str | None = None
    annotation_value_json: str | None = None


class StructuredAnnotationComparison(ContractModel):
    prediction_id: UUID
    annotation_revision_id: UUID | None = None
    differences: tuple[SemanticAnnotationDiff, ...]
    added_count: StrictInt = Field(ge=0)
    removed_count: StrictInt = Field(ge=0)
    modified_count: StrictInt = Field(ge=0)
    unchanged_count: StrictInt = Field(ge=0)


def _validate_relationship_types(
    relationship: ScientificRelationship,
    entity_by_id: Mapping[UUID, ScientificEntityBase],
) -> None:
    subject = entity_by_id[relationship.subject_id]
    object_ = entity_by_id[relationship.object_id]
    allowed: dict[
        ScientificRelationshipType, tuple[type[ScientificEntityBase], type[ScientificEntityBase]]
    ] = {
        ScientificRelationshipType.LANE_CONTAINS_PROTEIN: (
            LaneConditionAnnotation,
            ProteinAnnotation,
        ),
        ScientificRelationshipType.LANE_RECEIVES_TREATMENT: (
            LaneConditionAnnotation,
            TreatmentAnnotation,
        ),
        ScientificRelationshipType.LANE_USES_BIOLOGICAL_CONTEXT: (
            LaneConditionAnnotation,
            BiologicalContextAnnotation,
        ),
        ScientificRelationshipType.LANE_HAS_REPLICATE: (
            LaneConditionAnnotation,
            ReplicateAnnotation,
        ),
        ScientificRelationshipType.PROTEIN_USES_LOADING_CONTROL: (
            ProteinAnnotation,
            ProteinAnnotation,
        ),
        ScientificRelationshipType.PROTEIN_DETECTED_BY_ANTIBODY: (
            ProteinAnnotation,
            AntibodyAnnotation,
        ),
        ScientificRelationshipType.PROTEIN_HAS_MOLECULAR_WEIGHT: (
            ProteinAnnotation,
            MolecularWeightAnnotation,
        ),
    }
    subject_type, object_type = allowed[relationship.relation_type]
    if not isinstance(subject, subject_type) or not isinstance(object_, object_type):
        raise ValueError(
            f"{relationship.relation_type.value} relationship has incompatible entity types"
        )
    if relationship.relation_type is ScientificRelationshipType.PROTEIN_USES_LOADING_CONTROL:
        assert isinstance(subject, ProteinAnnotation)
        assert isinstance(object_, ProteinAnnotation)
        if (
            subject.role is not ProteinRole.TARGET
            or object_.role is not ProteinRole.LOADING_CONTROL
        ):
            raise ValueError("loading-control relationships must connect target to loading control")
