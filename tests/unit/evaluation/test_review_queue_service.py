from datetime import UTC, datetime
from uuid import uuid4

import pytest
from hiveblot_contracts import (
    RegressionStatus,
    ReviewQueueCaseSummary,
    ReviewQueueFilters,
    ReviewStatus,
)
from hiveblot_evaluation import ConcurrencyConflict, ReviewQueueService

from tests.fakes.review_queue import InMemoryReviewQueueRepository

NOW = datetime(2026, 8, 2, 17, tzinfo=UTC)


def queue_case(index: int) -> ReviewQueueCaseSummary:
    return ReviewQueueCaseSummary(
        case_id=uuid4(),
        case_key=f"paper:figure-{index}:A",
        dataset_id=None,
        assay_type="western_blot",
        review_status=ReviewStatus.UNREVIEWED,
        case_version=1,
        source_label=f"figure-{index}.png",
        thumbnail_artifact_id=uuid4(),
        prediction_id=uuid4(),
        prediction_version="1.0",
        confidence=0.8,
        warning_count=0,
        missing_provenance=False,
        gold_eligible=False,
        model_disagreement=False,
        regression_status=RegressionStatus.NOT_EVALUATED,
        exclusively_assigned=False,
        last_reviewer_id=None,
        last_reviewed_at=None,
        updated_at=NOW,
    )


def test_review_queue_paginates_without_materializing_all_cases() -> None:
    repository = InMemoryReviewQueueRepository(tuple(queue_case(index) for index in range(120)))
    service = ReviewQueueService(repository)

    page = service.browse(ReviewQueueFilters(), limit=50, offset=50)

    assert len(page.items) == 50
    assert page.total == 120
    assert page.next_offset == 100
    assert page.items[0].case_key == "paper:figure-50:A"


def test_saved_views_use_optimistic_versions_and_remain_owner_scoped() -> None:
    repository = InMemoryReviewQueueRepository()
    service = ReviewQueueService(repository, clock=lambda: NOW)
    owner_id = uuid4()
    view = service.create_view(
        owner_id=owner_id,
        name=" Unreviewed ",
        filters=ReviewQueueFilters(review_statuses=(ReviewStatus.UNREVIEWED,)),
    )
    updated = service.update_view(
        view.view_id,
        owner_id=owner_id,
        expected_version=1,
        name="Needs provenance",
        filters=ReviewQueueFilters(missing_provenance=True),
    )

    assert view.name == "Unreviewed"
    assert updated.version == 2
    assert service.list_views(owner_id) == (updated,)
    with pytest.raises(ConcurrencyConflict):
        service.update_view(
            view.view_id,
            owner_id=owner_id,
            expected_version=1,
            name="Stale",
            filters=ReviewQueueFilters(),
        )
