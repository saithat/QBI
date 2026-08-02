from datetime import UTC, datetime
from uuid import uuid4

import httpx
import pytest
from hiveblot_contracts import (
    RegressionStatus,
    ReviewQueueCaseSummary,
    ReviewStatus,
)
from hiveblot_evaluation import ReviewQueueService

from apps.api.main import app
from apps.api.review_queue_dependencies import get_review_queue_service
from tests.fakes.review_queue import InMemoryReviewQueueRepository

NOW = datetime(2026, 8, 2, 17, tzinfo=UTC)


@pytest.mark.asyncio
async def test_review_browser_filters_persists_views_and_serves_ui() -> None:
    repository = InMemoryReviewQueueRepository(
        (
            _case(ReviewStatus.UNREVIEWED, missing_provenance=True),
            _case(ReviewStatus.REVIEWED, missing_provenance=False),
        )
    )
    service = ReviewQueueService(repository, clock=lambda: NOW)
    app.dependency_overrides[get_review_queue_service] = lambda: service
    owner_id = uuid4()
    transport = httpx.ASGITransport(app=app)
    try:
        async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
            browser = await client.get("/review")
            javascript = await client.get("/review/assets/review.js")
            queue = await client.get(
                "/api/v1/review-queue",
                params={"review_status": "unreviewed", "missing_provenance": "true"},
            )
            invalid_range = await client.get(
                "/api/v1/review-queue",
                params={"confidence_min": "0.9", "confidence_max": "0.2"},
            )
            created = await client.post(
                "/api/v1/review-views",
                json={
                    "schema_version": "1.0",
                    "owner_id": str(owner_id),
                    "name": "Missing provenance",
                    "filters": {
                        "schema_version": "1.0",
                        "review_statuses": ["unreviewed"],
                        "missing_provenance": True,
                    },
                },
            )
            duplicate = await client.post(
                "/api/v1/review-views",
                json={
                    "schema_version": "1.0",
                    "owner_id": str(owner_id),
                    "name": "Missing provenance",
                    "filters": {"schema_version": "1.0"},
                },
            )
            views = await client.get("/api/v1/review-views", params={"owner_id": owner_id})

        assert browser.status_code == 200
        assert "Review queue" in browser.text
        assert javascript.status_code == 200
        assert "URLSearchParams" in javascript.text
        assert queue.status_code == 200
        assert invalid_range.status_code == 422
        assert queue.json()["total"] == 1
        assert queue.json()["items"][0]["missing_provenance"] is True
        assert repository.last_filters is not None
        assert repository.last_filters.review_statuses == (ReviewStatus.UNREVIEWED,)
        assert created.status_code == 201
        assert duplicate.status_code == 409
        assert views.json()["views"][0]["name"] == "Missing provenance"
    finally:
        app.dependency_overrides.pop(get_review_queue_service, None)


def _case(status: ReviewStatus, *, missing_provenance: bool) -> ReviewQueueCaseSummary:
    return ReviewQueueCaseSummary(
        case_id=uuid4(),
        case_key=f"paper:{status.value}:{uuid4()}",
        dataset_id=None,
        assay_type="western_blot",
        review_status=status,
        case_version=1,
        source_label="https://repository.example/figure.png",
        thumbnail_artifact_id=uuid4(),
        prediction_id=uuid4(),
        prediction_version="1.0",
        confidence=0.8,
        warning_count=1 if missing_provenance else 0,
        missing_provenance=missing_provenance,
        gold_eligible=False,
        model_disagreement=False,
        regression_status=RegressionStatus.NOT_EVALUATED,
        exclusively_assigned=False,
        last_reviewer_id=None,
        last_reviewed_at=None,
        updated_at=NOW,
    )
