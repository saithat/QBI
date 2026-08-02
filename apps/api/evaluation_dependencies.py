"""Composition root for evaluation and annotation persistence."""

from functools import lru_cache

from hiveblot_evaluation import EvaluationService, PostgresEvaluationRepository

from hiveblot.settings import get_settings


@lru_cache(maxsize=1)
def get_evaluation_service() -> EvaluationService:
    return EvaluationService(PostgresEvaluationRepository(get_settings().database_url))
