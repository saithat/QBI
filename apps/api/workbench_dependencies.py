"""Composition root for source evidence aggregation."""

from functools import lru_cache

from hiveblot_evaluation import EvidenceWorkbenchService, PostgresSourceContextRepository

from hiveblot.settings import get_settings

from .artifact_dependencies import get_artifact_service
from .evaluation_dependencies import get_evaluation_service


@lru_cache(maxsize=1)
def get_workbench_service() -> EvidenceWorkbenchService:
    return EvidenceWorkbenchService(
        get_evaluation_service(),
        get_artifact_service(),
        PostgresSourceContextRepository(get_settings().database_url),
    )
