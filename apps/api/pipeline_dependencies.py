"""Composition root for versioned pipeline runs and selective replay."""

from functools import lru_cache

from hiveblot_evaluation import PipelineRegistryService, PostgresPipelineRunRepository

from hiveblot.settings import get_settings

from .artifact_dependencies import get_artifact_service
from .evaluation_dependencies import get_evaluation_service


@lru_cache(maxsize=1)
def get_pipeline_registry_service() -> PipelineRegistryService:
    return PipelineRegistryService(
        PostgresPipelineRunRepository(get_settings().database_url),
        get_evaluation_service(),
        get_artifact_service(),
    )
