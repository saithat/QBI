from uuid import uuid4

import pytest
from hiveblot_contracts import (
    ProteinAnnotation,
    ProteinRole,
    ScientificRelationship,
    ScientificRelationshipType,
    WesternBlotStructuredAnnotation,
)
from pydantic import ValidationError

from tests.fakes.structured import structured_annotation


def test_complete_western_blot_annotation_round_trips_strictly() -> None:
    annotation = structured_annotation()

    restored = WesternBlotStructuredAnnotation.model_validate_json(annotation.model_dump_json())

    assert restored == annotation
    assert restored.proteins[0].canonical_reference.accession == "P04637"
    assert restored.treatments[0].dose.unit == "ng/mL"
    assert restored.replicates[0].biological_replicates == 3


def test_unknown_fields_and_incomplete_present_entities_are_rejected() -> None:
    payload = structured_annotation().model_dump(mode="json")
    payload["unexpected"] = True
    with pytest.raises(ValidationError, match="extra_forbidden"):
        WesternBlotStructuredAnnotation.model_validate(payload)

    with pytest.raises(ValidationError, match="require a name"):
        ProteinAnnotation(entity_id=uuid4(), role=ProteinRole.TARGET)


def test_relationship_graph_rejects_unknown_and_incompatible_entities() -> None:
    annotation = structured_annotation()
    target = annotation.proteins[0]
    lane = annotation.lane_conditions[0]
    invalid = ScientificRelationship(
        relationship_id=uuid4(),
        subject_id=target.entity_id,
        relation_type=ScientificRelationshipType.LANE_CONTAINS_PROTEIN,
        object_id=lane.entity_id,
    )

    with pytest.raises(ValidationError, match="incompatible entity types"):
        WesternBlotStructuredAnnotation.model_validate(
            annotation.model_copy(update={"relationships": (invalid,)}).model_dump(mode="python")
        )
