"""In-memory metric-run persistence for deterministic service and API tests."""

from uuid import UUID

from hiveblot_contracts import EvaluationMetricRunRecord
from hiveblot_evaluation import DuplicateEvaluationRecord


class InMemoryEvaluationMetricRunRepository:
    def __init__(self) -> None:
        self.records: dict[UUID, EvaluationMetricRunRecord] = {}

    def create(self, record: EvaluationMetricRunRecord) -> EvaluationMetricRunRecord:
        if record.metric_run_id in self.records:
            raise DuplicateEvaluationRecord(f"metric run {record.metric_run_id} already exists")
        self.records[record.metric_run_id] = record
        return record

    def get(self, metric_run_id: UUID) -> EvaluationMetricRunRecord | None:
        return self.records.get(metric_run_id)

    def list(
        self,
        *,
        dataset_name: str | None,
        dataset_version: str | None,
        pipeline_name: str | None,
        pipeline_version: str | None,
        limit: int,
    ):
        records = (
            item
            for item in self.records.values()
            if (dataset_name is None or item.dataset_name == dataset_name)
            and (dataset_version is None or item.dataset_version == dataset_version)
            and (pipeline_name is None or item.pipeline.name == pipeline_name)
            and (pipeline_version is None or item.pipeline.version == pipeline_version)
        )
        return tuple(
            sorted(
                records, key=lambda item: (item.created_at, str(item.metric_run_id)), reverse=True
            )[:limit]
        )
