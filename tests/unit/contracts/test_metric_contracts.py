from uuid import uuid4

import pytest
from hiveblot_contracts import (
    EvaluationDatasetSnapshot,
    EvaluationScoringInput,
    PipelineEvaluationSubmission,
    PredictedFieldObservation,
    PredictedObservationDocument,
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


def test_prediction_observation_keys_and_submission_cases_are_unique() -> None:
    field = PredictedFieldObservation(field_path="/target", value_json='"TP53"')
    with pytest.raises(ValidationError, match="field paths must be unique"):
        PredictedObservationDocument(fields=(field, field))

    fixture = scoring_input()
    duplicate = (*fixture.submission.cases, fixture.submission.cases[0])
    with pytest.raises(ValidationError, match="case IDs"):
        PipelineEvaluationSubmission(
            pipeline=fixture.submission.pipeline,
            model_versions=fixture.submission.model_versions,
            cases=duplicate,
        )


def test_metric_contracts_reject_unknown_fields_and_coercion() -> None:
    with pytest.raises(ValidationError):
        PredictedFieldObservation.model_validate(
            {"field_path": "/target", "value_json": '"TP53"', "confidence": "0.9"}
        )
    with pytest.raises(ValidationError):
        PredictedFieldObservation.model_validate(
            {
                "field_path": "/target",
                "value_json": '"TP53"',
                "unknown": str(uuid4()),
            }
        )
