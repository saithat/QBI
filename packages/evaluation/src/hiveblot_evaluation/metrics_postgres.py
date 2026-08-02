"""PostgreSQL persistence for immutable versioned evaluation metric runs."""

from __future__ import annotations

import json
from collections.abc import Sequence
from uuid import UUID

import psycopg
from hiveblot_contracts import EvaluationMetricRunRecord
from psycopg import errors
from psycopg.rows import dict_row
from psycopg.types.json import Jsonb

from .errors import DuplicateEvaluationRecord


class PostgresEvaluationMetricRunRepository:
    def __init__(self, database_url: str) -> None:
        self._database_url = database_url

    def create(self, record: EvaluationMetricRunRecord) -> EvaluationMetricRunRecord:
        try:
            with psycopg.connect(self._database_url) as connection:
                connection.execute(
                    """
                    INSERT INTO evaluation_metric_runs (
                        metric_run_id, dataset_snapshot_id, dataset_name, dataset_version,
                        dataset_sha256, scorer_name, scorer_version, pipeline_name,
                        pipeline_version, input_sha256, configuration_json,
                        metric_run_json, created_at
                    ) VALUES (
                        %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s
                    )
                    """,
                    (
                        record.metric_run_id,
                        record.dataset_snapshot_id,
                        record.dataset_name,
                        record.dataset_version,
                        record.dataset_sha256,
                        record.scorer.name,
                        record.scorer.version,
                        record.pipeline.name,
                        record.pipeline.version,
                        record.input_sha256,
                        Jsonb(record.configuration.model_dump(mode="json")),
                        Jsonb(record.model_dump(mode="json")),
                        record.created_at,
                    ),
                )
                for case in record.cases:
                    connection.execute(
                        """
                        INSERT INTO evaluation_metric_case_results (
                            metric_run_id, case_id, composite_score, case_result_json
                        ) VALUES (%s, %s, %s, %s)
                        """,
                        (
                            record.metric_run_id,
                            case.case_id,
                            case.metrics.composite_score,
                            Jsonb(case.model_dump(mode="json")),
                        ),
                    )
        except errors.UniqueViolation as exc:
            raise DuplicateEvaluationRecord(
                f"evaluation metric run {record.metric_run_id} already exists"
            ) from exc
        return record

    def get(self, metric_run_id: UUID) -> EvaluationMetricRunRecord | None:
        with psycopg.connect(self._database_url, row_factory=dict_row) as connection:
            row = connection.execute(
                """
                SELECT metric_run_json FROM evaluation_metric_runs
                WHERE metric_run_id = %s
                """,
                (metric_run_id,),
            ).fetchone()
        return _record(row["metric_run_json"]) if row else None

    def list(
        self,
        *,
        dataset_name: str | None,
        dataset_version: str | None,
        pipeline_name: str | None,
        pipeline_version: str | None,
        limit: int,
    ) -> Sequence[EvaluationMetricRunRecord]:
        clauses: list[str] = []
        parameters: list[object] = []
        for column, value in (
            ("dataset_name", dataset_name),
            ("dataset_version", dataset_version),
            ("pipeline_name", pipeline_name),
            ("pipeline_version", pipeline_version),
        ):
            if value is not None:
                clauses.append(f"{column} = %s")
                parameters.append(value)
        where = f"WHERE {' AND '.join(clauses)}" if clauses else ""
        parameters.append(limit)
        with psycopg.connect(self._database_url, row_factory=dict_row) as connection:
            rows = connection.execute(
                f"""
                SELECT metric_run_json FROM evaluation_metric_runs
                {where}
                ORDER BY created_at DESC, metric_run_id DESC
                LIMIT %s
                """,
                parameters,
            ).fetchall()
        return tuple(_record(row["metric_run_json"]) for row in rows)


def _record(value: object) -> EvaluationMetricRunRecord:
    return EvaluationMetricRunRecord.model_validate_json(json.dumps(value))
