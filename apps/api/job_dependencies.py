"""Composition root for the generic durable job service."""

from functools import lru_cache

from hiveblot_job_service import JobService, PostgresJobRepository

from hiveblot.settings import get_settings

from .artifact_dependencies import get_artifact_service


@lru_cache(maxsize=1)
def get_job_service() -> JobService:
    settings = get_settings()
    return JobService(
        PostgresJobRepository(settings.database_url),
        get_artifact_service(),
    )
