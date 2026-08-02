"""Composition root for the review-queue projection."""

from functools import lru_cache

from hiveblot_evaluation import PostgresReviewQueueRepository, ReviewQueueService

from hiveblot.settings import get_settings


@lru_cache(maxsize=1)
def get_review_queue_service() -> ReviewQueueService:
    return ReviewQueueService(PostgresReviewQueueRepository(get_settings().database_url))
