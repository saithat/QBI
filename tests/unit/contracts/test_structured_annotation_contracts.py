from uuid import uuid4

import pytest
from hiveblot_contracts import (
    ScientificRelationship,
    ScientificRelationshipType,
    WesternBlotStructuredAnnotation,
)
from pydantic import ValidationError

from tests.fakes.structured import structured_annotation


def test_relationship_graph_rejects_incompatible_entities() -> None:
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
