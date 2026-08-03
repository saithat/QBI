"""Composition root for public discovery and crawl-frontier state."""

from functools import lru_cache

from hiveblot_crawler import (
    FetchQueueService,
    FrontierService,
    PostgresFetchRepository,
    PostgresFrontierRepository,
)

from hiveblot.settings import get_settings

from .artifact_dependencies import get_artifact_service


@lru_cache(maxsize=1)
def get_frontier_service() -> FrontierService:
    settings = get_settings()
    return FrontierService(
        PostgresFrontierRepository(settings.database_url),
        get_artifact_service(),
    )


@lru_cache(maxsize=1)
def get_fetch_queue_service() -> FetchQueueService:
    settings = get_settings()
    return FetchQueueService(PostgresFetchRepository(settings.database_url))
