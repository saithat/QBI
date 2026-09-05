import pytest
from hiveblot_contracts import (
    EvaluationDatasetSnapshot,
    EvaluationScoringInput,
)
from pydantic import ValidationError

from tests.fakes.metrics import scoring_input


def test_frozen_dataset_hash_and_scoring_case_membership_are_enforced() -> None:
    fixture = scoring_input()

    with pytest.raises(ValidationError, match="content_sha256 does not match"):
        EvaluationDatasetSnapshot.model_validate(
            fixture.dataset.model_copy(update={"content_sha256": "0" * 64}).model_dump(
                mode="python"
            )
        )

    missing_case = fixture.submission.model_copy(update={"cases": fixture.submission.cases[:1]})
    with pytest.raises(ValidationError, match="exactly match"):
        EvaluationScoringInput.model_validate(
            fixture.model_copy(update={"submission": missing_case}).model_dump(mode="python")
        )
