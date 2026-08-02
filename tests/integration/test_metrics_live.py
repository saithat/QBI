"""Opt-in PostgreSQL acceptance test for immutable evaluation metric runs."""

import os

import psycopg
import pytest
from hiveblot_contracts import CalibrationCategory
from hiveblot_evaluation import (
    EvaluationMetricsService,
    PostgresEvaluationMetricRunRepository,
)

from hiveblot import db
from hiveblot.settings import Settings
from tests.fakes.metrics import CASE_TWO, scoring_input

pytestmark = pytest.mark.skipif(
    os.getenv("HIVEBLOT_RUN_LIVE_METRICS") != "1",
    reason="set HIVEBLOT_RUN_LIVE_METRICS=1 with local PostgreSQL",
)


def test_live_metric_runs_reload_filter_calibrate_and_compare() -> None:
    settings = Settings(_env_file=None)
    db.initialize(settings.database_url)
    repository = PostgresEvaluationMetricRunRepository(settings.database_url)
    service = EvaluationMetricsService(repository)
    baseline = service.score(scoring_input())
    candidate = service.score(scoring_input(candidate=True))

    try:
        reloaded_service = EvaluationMetricsService(
            PostgresEvaluationMetricRunRepository(settings.database_url)
        )
        reloaded = reloaded_service.get(candidate.metric_run_id)
        filtered = reloaded_service.list(
            dataset_name="western-blot-frozen",
            dataset_version="2026.08",
            pipeline_name="western-blot-extraction",
            pipeline_version="2.0",
            limit=10,
        )
        calibration = reloaded_service.calibration(
            candidate.metric_run_id,
            category=CalibrationCategory.FIELD,
        )
        comparison = reloaded_service.compare(
            baseline.metric_run_id,
            candidate.metric_run_id,
            minimum_delta=0.01,
        )

        assert reloaded == candidate
        assert any(item.metric_run_id == candidate.metric_run_id for item in filtered)
        assert calibration.calibration.observations == 2
        assert reloaded_service.get_case(candidate.metric_run_id, CASE_TWO).case_id == CASE_TWO
        assert comparison.regressions == (CASE_TWO,)
    finally:
        run_ids = (baseline.metric_run_id, candidate.metric_run_id)
        with psycopg.connect(settings.database_url) as connection:
            connection.execute(
                "DELETE FROM evaluation_metric_case_results WHERE metric_run_id = ANY(%s)",
                (list(run_ids),),
            )
            connection.execute(
                "DELETE FROM evaluation_metric_runs WHERE metric_run_id = ANY(%s)",
                (list(run_ids),),
            )
