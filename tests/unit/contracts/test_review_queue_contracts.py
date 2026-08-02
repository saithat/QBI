from datetime import UTC, datetime
from uuid import uuid4

import pytest
from hiveblot_contracts import (
    RegressionStatus,
    ReviewQueueCaseSummary,
    ReviewQueueFilters,
    ReviewStatus,
)
from pydantic import ValidationError


def test_review_queue_filters_are_strict_and_validate_confidence_range() -> None:
    filters = ReviewQueueFilters(
        review_statuses=(ReviewStatus.UNREVIEWED,),
        confidence_min=0.2,
        confidence_max=0.8,
        missing_provenance=True,
    )

    assert filters.schema_version == "1.0"
    with pytest.raises(ValidationError, match="confidence_min"):
        ReviewQueueFilters(confidence_min=0.9, confidence_max=0.3)
    with pytest.raises(ValidationError, match="Extra inputs"):
        ReviewQueueFilters.model_validate({"schema_version": "1.0", "unknown": True})


def test_review_queue_summary_rejects_coerced_confidence() -> None:
    with pytest.raises(ValidationError):
        ReviewQueueCaseSummary(
            case_id=uuid4(),
            case_key="paper:figure:1",
            dataset_id=None,
            assay_type="western_blot",
            review_status=ReviewStatus.UNREVIEWED,
            case_version=1,
            source_label="figure.png",
            thumbnail_artifact_id=uuid4(),
            prediction_id=uuid4(),
            prediction_version="1.0",
            confidence="0.8",
            warning_count=0,
            missing_provenance=False,
            gold_eligible=False,
            model_disagreement=False,
            regression_status=RegressionStatus.NOT_EVALUATED,
            exclusively_assigned=False,
            last_reviewer_id=None,
            last_reviewed_at=None,
            updated_at=datetime(2026, 8, 2, tzinfo=UTC),
        )
