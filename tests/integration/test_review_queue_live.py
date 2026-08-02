from __future__ import annotations

import hashlib
import os
import time
from datetime import UTC, datetime
from uuid import uuid4

import psycopg
import pytest
from hiveblot_contracts import ReviewQueueFilters, ReviewStatus
from hiveblot_evaluation import (
    ConcurrencyConflict,
    EvaluationService,
    PostgresEvaluationRepository,
    PostgresReviewQueueRepository,
    ReviewQueueService,
)

from hiveblot.db import initialize
from hiveblot.settings import get_settings

pytestmark = pytest.mark.skipif(
    os.environ.get("HIVEBLOT_RUN_LIVE_REVIEW_QUEUE") != "1",
    reason="set HIVEBLOT_RUN_LIVE_REVIEW_QUEUE=1 with local PostgreSQL",
)


def test_postgres_review_queue_remains_paginated_with_ten_thousand_cases() -> None:
    settings = get_settings()
    initialize(settings.database_url)
    dataset_id = uuid4()
    artifact_id = uuid4()
    case_ids = tuple(uuid4() for _ in range(10_000))
    now = datetime(2026, 8, 2, 18, tzinfo=UTC)
    blob_hash = hashlib.sha256(dataset_id.bytes).hexdigest()

    try:
        with psycopg.connect(settings.database_url) as connection:
            connection.execute(
                """
                INSERT INTO artifact_blobs (sha256, media_type, byte_size, storage_key, created_at)
                VALUES (%s, 'image/png', 1, %s, %s)
                """,
                (blob_hash, f"live-review-queue/{blob_hash}", now),
            )
            connection.execute(
                """
                INSERT INTO artifacts (
                    artifact_id, blob_sha256, original_filename, source_uri,
                    acquisition_method, visibility, organization_id, created_at
                ) VALUES (%s, %s, 'figure.png', %s, 'source_adapter', 'public', NULL, %s)
                """,
                (
                    artifact_id,
                    blob_hash,
                    f"https://review-queue.test/{dataset_id}/figure.png",
                    now,
                ),
            )
            with connection.cursor() as cursor:
                cursor.executemany(
                    """
                    INSERT INTO evaluation_cases (
                        case_id, case_key, dataset_id, assay_type, review_status,
                        version, created_at, updated_at
                    ) VALUES (%s, %s, %s, 'western_blot', %s, 1, %s, %s)
                    """,
                    (
                        (
                            case_id,
                            f"load:{dataset_id}:{index}",
                            dataset_id,
                            "unreviewed" if index % 2 == 0 else "reviewed",
                            now,
                            now,
                        )
                        for index, case_id in enumerate(case_ids)
                    ),
                )
                cursor.executemany(
                    """
                    INSERT INTO evaluation_case_artifacts (
                        case_id, artifact_id, artifact_role, page_number
                    ) VALUES (%s, %s, 'figure', 1)
                    """,
                    ((case_id, artifact_id) for case_id in case_ids),
                )
                cursor.executemany(
                    """
                    INSERT INTO evaluation_case_review_metadata (
                        case_id, gold_eligible, regression_status, updated_at
                    ) VALUES (%s, TRUE, 'not_evaluated', %s)
                    """,
                    ((case_id, now) for index, case_id in enumerate(case_ids) if index % 10 == 0),
                )

        service = ReviewQueueService(PostgresReviewQueueRepository(settings.database_url))
        started = time.perf_counter()
        page = service.browse(
            ReviewQueueFilters(
                review_statuses=(ReviewStatus.UNREVIEWED,),
                dataset_id=dataset_id,
                source_query="review-queue.test",
                missing_provenance=True,
                gold_eligible=True,
            ),
            limit=50,
            offset=0,
        )
        elapsed = time.perf_counter() - started

        assert page.total == 1_000
        assert len(page.items) == 50
        assert page.next_offset == 50
        assert all(item.dataset_id == dataset_id for item in page.items)
        assert elapsed < 10

        evaluation = EvaluationService(PostgresEvaluationRepository(settings.database_url))
        evaluation.assign_reviewer(page.items[0].case_id, reviewer_id=uuid4(), exclusive=True)
        with pytest.raises(ConcurrencyConflict):
            evaluation.assign_reviewer(page.items[0].case_id, reviewer_id=uuid4(), exclusive=False)
    finally:
        with psycopg.connect(settings.database_url) as connection:
            connection.execute(
                """
                DELETE FROM reviewer_assignments
                WHERE case_id IN (SELECT case_id FROM evaluation_cases WHERE dataset_id = %s)
                """,
                (dataset_id,),
            )
            connection.execute(
                """
                DELETE FROM evaluation_case_review_metadata
                WHERE case_id IN (SELECT case_id FROM evaluation_cases WHERE dataset_id = %s)
                """,
                (dataset_id,),
            )
            connection.execute(
                """
                DELETE FROM evaluation_case_artifacts
                WHERE case_id IN (SELECT case_id FROM evaluation_cases WHERE dataset_id = %s)
                """,
                (dataset_id,),
            )
            connection.execute("DELETE FROM evaluation_cases WHERE dataset_id = %s", (dataset_id,))
            connection.execute("DELETE FROM artifacts WHERE artifact_id = %s", (artifact_id,))
            connection.execute("DELETE FROM artifact_blobs WHERE sha256 = %s", (blob_hash,))
