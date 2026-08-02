"""Persistence boundary for immutable evaluation metric runs."""

from __future__ import annotations

from collections.abc import Sequence
from typing import Protocol
from uuid import UUID

from hiveblot_contracts import EvaluationMetricRunRecord


class EvaluationMetricRunRepository(Protocol):
    def create(self, record: EvaluationMetricRunRecord) -> EvaluationMetricRunRecord: ...

    def get(self, metric_run_id: UUID) -> EvaluationMetricRunRecord | None: ...

    def list(
        self,
        *,
        dataset_name: str | None,
        dataset_version: str | None,
        pipeline_name: str | None,
        pipeline_version: str | None,
        limit: int,
    ) -> Sequence[EvaluationMetricRunRecord]: ...
