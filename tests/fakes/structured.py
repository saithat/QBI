"""Deterministic builders for typed western-blot annotations."""

from uuid import UUID, uuid4

from hiveblot_contracts import (
    AntibodyAnnotation,
    BiologicalContextAnnotation,
    BiologicalContextType,
    CanonicalEntityReference,
    CanonicalEntityType,
    LaneConditionAnnotation,
    MolecularWeightAnnotation,
    ProteinAnnotation,
    ProteinRole,
    Quantity,
    ReplicateAnnotation,
    ScientificRelationship,
    ScientificRelationshipType,
    TreatmentAnnotation,
    WesternBlotStructuredAnnotation,
)


def structured_annotation(
    target_name: str = "TP53",
    *,
    target_id: UUID | None = None,
    reviewer_notes: str | None = "Validated against the figure caption.",
) -> WesternBlotStructuredAnnotation:
    target_id = target_id or uuid4()
    control_id = uuid4()
    lane_id = uuid4()
    context_id = uuid4()
    treatment_id = uuid4()
    antibody_id = uuid4()
    weight_id = uuid4()
    replicate_id = uuid4()
    return WesternBlotStructuredAnnotation(
        proteins=(
            ProteinAnnotation(
                entity_id=target_id,
                role=ProteinRole.TARGET,
                name=target_name,
                original_extracted_text="p53",
                canonical_reference=CanonicalEntityReference(
                    entity_type=CanonicalEntityType.PROTEIN,
                    canonical_id="uniprot:P04637",
                    label="Cellular tumor antigen p53 (TP53)",
                    vocabulary="UniProtKB",
                    accession="P04637",
                ),
            ),
            ProteinAnnotation(
                entity_id=control_id,
                role=ProteinRole.LOADING_CONTROL,
                name="ACTB",
                original_extracted_text="β-actin",
            ),
        ),
        biological_contexts=(
            BiologicalContextAnnotation(
                entity_id=context_id,
                context_type=BiologicalContextType.CELL_LINE,
                name="HeLa",
                original_extracted_text="HeLa cells",
            ),
        ),
        treatments=(
            TreatmentAnnotation(
                entity_id=treatment_id,
                name="EGF",
                dose=Quantity(value=10.0, unit="ng/mL"),
                duration=Quantity(value=30.0, unit="min"),
                original_extracted_text="10 ng/mL EGF for 30 min",
            ),
        ),
        lane_conditions=(
            LaneConditionAnnotation(
                entity_id=lane_id,
                lane_index=1,
                lane_label="Lane 1",
                condition_label="EGF-treated HeLa",
            ),
        ),
        antibodies=(
            AntibodyAnnotation(
                entity_id=antibody_id,
                name="anti-p53",
                vendor="Example Biosciences",
                catalog_number="AB-53",
                dilution="1:1000",
            ),
        ),
        molecular_weights=(MolecularWeightAnnotation(entity_id=weight_id, value_kda=53.0),),
        replicates=(ReplicateAnnotation(entity_id=replicate_id, biological_replicates=3),),
        relationships=(
            _relationship(lane_id, ScientificRelationshipType.LANE_CONTAINS_PROTEIN, target_id),
            _relationship(
                lane_id,
                ScientificRelationshipType.LANE_USES_BIOLOGICAL_CONTEXT,
                context_id,
            ),
            _relationship(
                lane_id,
                ScientificRelationshipType.LANE_RECEIVES_TREATMENT,
                treatment_id,
            ),
            _relationship(lane_id, ScientificRelationshipType.LANE_HAS_REPLICATE, replicate_id),
            _relationship(
                target_id,
                ScientificRelationshipType.PROTEIN_USES_LOADING_CONTROL,
                control_id,
            ),
            _relationship(
                target_id,
                ScientificRelationshipType.PROTEIN_DETECTED_BY_ANTIBODY,
                antibody_id,
            ),
            _relationship(
                target_id,
                ScientificRelationshipType.PROTEIN_HAS_MOLECULAR_WEIGHT,
                weight_id,
            ),
        ),
        reviewer_notes=reviewer_notes,
    )


def _relationship(
    subject_id: UUID,
    relation_type: ScientificRelationshipType,
    object_id: UUID,
) -> ScientificRelationship:
    return ScientificRelationship(
        relationship_id=uuid4(),
        subject_id=subject_id,
        relation_type=relation_type,
        object_id=object_id,
    )
