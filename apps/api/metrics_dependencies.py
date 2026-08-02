"""Composition root for reproducible evaluation metric runs."""

from functools import lru_cache

from hiveblot_evaluation import (
    EvaluationMetricsService,
    PostgresEvaluationMetricRunRepository,
)

from hiveblot.settings import get_settings


@lru_cache(maxsize=1)
def get_evaluation_metrics_service() -> EvaluationMetricsService:
    return EvaluationMetricsService(
        PostgresEvaluationMetricRunRepository(get_settings().database_url)
    )
