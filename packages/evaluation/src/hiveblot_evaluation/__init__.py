"""Evaluation cases and append-only scientific review persistence."""

from .errors import (
    ConcurrencyConflict,
    DuplicateEvaluationRecord,
    EvaluationError,
    EvaluationNotFound,
    InvalidEvaluationState,
)
from .metrics import (
    EvaluationMetricsService,
    compare_metric_runs,
    evaluation_scoring_input_sha256,
    score_evaluation,
)
from .metrics_postgres import PostgresEvaluationMetricRunRepository
from .metrics_repository import EvaluationMetricRunRepository
from .pipeline import (
    OutputSchemaRegistry,
    PipelineArtifactLookup,
    PipelineRegistryService,
)
from .pipeline_postgres import PostgresPipelineRunRepository
from .pipeline_repository import PipelineRunRepository
from .postgres import PostgresEvaluationRepository
from .repository import EvaluationRepository
from .review_queue import ReviewQueueRepository, ReviewQueueService
from .review_queue_postgres import PostgresReviewQueueRepository
from .service import EvaluationService
from .spatial import SpatialAnnotationService
from .structured import CanonicalEntityCatalog, StructuredAnnotationService
from .workbench import ArtifactLookup, EvidenceWorkbenchService, SourceContextRepository
from .workbench_postgres import PostgresSourceContextRepository

__all__ = [
    "ConcurrencyConflict",
    "CanonicalEntityCatalog",
    "DuplicateEvaluationRecord",
    "EvaluationError",
    "EvaluationMetricRunRepository",
    "EvaluationMetricsService",
    "EvaluationNotFound",
    "EvaluationRepository",
    "EvaluationService",
    "EvidenceWorkbenchService",
    "InvalidEvaluationState",
    "OutputSchemaRegistry",
    "PipelineArtifactLookup",
    "PipelineRegistryService",
    "PipelineRunRepository",
    "PostgresEvaluationRepository",
    "PostgresEvaluationMetricRunRepository",
    "PostgresPipelineRunRepository",
    "PostgresReviewQueueRepository",
    "PostgresSourceContextRepository",
    "ReviewQueueRepository",
    "ReviewQueueService",
    "SourceContextRepository",
    "SpatialAnnotationService",
    "StructuredAnnotationService",
    "ArtifactLookup",
    "compare_metric_runs",
    "evaluation_scoring_input_sha256",
    "score_evaluation",
]
