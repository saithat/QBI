from datetime import UTC, datetime
from uuid import uuid4

import pytest
from hiveblot_contracts import (
    AnnotationRevision,
    EntityFieldTarget,
    FieldAnnotation,
    FieldPathTarget,
    ObservationState,
)
from pydantic import ValidationError


def test_annotation_target_is_a_strict_discriminated_union() -> None:
    field = FieldAnnotation(
        field_annotation_id=uuid4(),
        target=EntityFieldTarget(entity_id=uuid4(), field_name="target"),
        state=ObservationState.AMBIGUOUS,
        value="p53/TP53",
    )

    assert field.target.target_type == "entity"
    with pytest.raises(ValidationError, match="Extra inputs are not permitted"):
        FieldPathTarget.model_validate(
            {
                "field_path": "/targets/0/name",
                "unexpected": True,
            }
        )


@pytest.mark.parametrize(
    "state",
    [
        ObservationState.PRESENT,
        ObservationState.ABSENT,
        ObservationState.UNKNOWN,
        ObservationState.AMBIGUOUS,
        ObservationState.NOT_APPLICABLE,
    ],
)
def test_all_explicit_annotation_states_round_trip(state: ObservationState) -> None:
    annotation = FieldAnnotation(
        field_annotation_id=uuid4(),
        target=FieldPathTarget(field_path="/targets/0/name"),
        state=state,
        value="p53"
        if state not in {ObservationState.ABSENT, ObservationState.NOT_APPLICABLE}
        else None,
    )

    assert annotation.state is state


def test_revision_rejects_duplicate_stable_entity_ids() -> None:
    field_id = uuid4()
    field = FieldAnnotation(
        field_annotation_id=field_id,
        target=FieldPathTarget(field_path="/targets/0/name"),
        state=ObservationState.PRESENT,
        value="p53",
    )

    with pytest.raises(ValidationError, match="IDs must be unique"):
        AnnotationRevision(
            revision_id=uuid4(),
            annotation_id=uuid4(),
            revision_number=1,
            reviewer_id=uuid4(),
            field_annotations=(field, field),
            created_at=datetime(2026, 8, 2, tzinfo=UTC),
        )
