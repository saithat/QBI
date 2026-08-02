"""Stable registry used to generate committed JSON Schema snapshots."""

from __future__ import annotations

import json
from collections.abc import Mapping
from typing import Any

from pydantic import TypeAdapter

from .annotations import (
    AdjudicationRecord,
    AnnotationDocumentRecord,
    AnnotationErrorCode,
    AnnotationRevision,
    EvaluationCaseRecord,
    PredictionDocument,
    ReviewerAssignment,
)
from .artifacts import ArtifactRecord, ArtifactReference, BoundingRegion, SourceDocumentRecord
from .evaluation import EvaluationCaseReference, ValidationIssue
from .evidence import CaseSourceContext, EvidenceOverlay, SourceEvidenceWorkbench
from .identifiers import ModelIdentifier, PipelineIdentifier, ToolIdentifier
from .jobs import JobResult, JobSpecification
from .metrics import (
    CalibrationSlice,
    EvaluationDatasetSnapshot,
    EvaluationMetricRunRecord,
    EvaluationScoringInput,
    PipelineComparisonRecord,
)
from .pipeline_runs import (
    ComponentInvocationRecord,
    ComponentResult,
    PipelineDefinitionRecord,
    PipelinePublicationRecord,
    PipelineRunDetail,
    PipelineRunRecord,
)
from .review_queue import (
    ReviewQueueCaseSummary,
    ReviewQueueFilters,
    ReviewQueuePage,
    SavedReviewView,
)
from .spatial_editing import SpatialAnnotationComparison, SpatialAnnotationSet
from .structured_annotations import (
    CanonicalEntityReference,
    StructuredAnnotationComparison,
    WesternBlotStructuredAnnotation,
)

JSON_SCHEMA_DIALECT = "https://json-schema.org/draft/2020-12/schema"
SCHEMA_ID_PREFIX = "urn:hiveblot:schema:v1:"

CONTRACT_REGISTRY: Mapping[str, Any] = {
    "adjudication-record": AdjudicationRecord,
    "annotation-document": AnnotationDocumentRecord,
    "annotation-error-code": AnnotationErrorCode,
    "annotation-revision": AnnotationRevision,
    "artifact-record": ArtifactRecord,
    "artifact-reference": ArtifactReference,
    "bounding-region": BoundingRegion,
    "calibration-slice": CalibrationSlice,
    "canonical-entity-reference": CanonicalEntityReference,
    "case-source-context": CaseSourceContext,
    "component-invocation-record": ComponentInvocationRecord,
    "component-result": ComponentResult,
    "evaluation-case-reference": EvaluationCaseReference,
    "evaluation-case-record": EvaluationCaseRecord,
    "evaluation-dataset-snapshot": EvaluationDatasetSnapshot,
    "evaluation-metric-run-record": EvaluationMetricRunRecord,
    "evaluation-scoring-input": EvaluationScoringInput,
    "evidence-overlay": EvidenceOverlay,
    "job-result": JobResult,
    "job-specification": JobSpecification,
    "model-identifier": ModelIdentifier,
    "pipeline-identifier": PipelineIdentifier,
    "pipeline-comparison-record": PipelineComparisonRecord,
    "pipeline-definition-record": PipelineDefinitionRecord,
    "pipeline-publication-record": PipelinePublicationRecord,
    "pipeline-run-detail": PipelineRunDetail,
    "pipeline-run-record": PipelineRunRecord,
    "prediction-document": PredictionDocument,
    "reviewer-assignment": ReviewerAssignment,
    "review-queue-case-summary": ReviewQueueCaseSummary,
    "review-queue-filters": ReviewQueueFilters,
    "review-queue-page": ReviewQueuePage,
    "saved-review-view": SavedReviewView,
    "source-document-record": SourceDocumentRecord,
    "source-evidence-workbench": SourceEvidenceWorkbench,
    "spatial-annotation-comparison": SpatialAnnotationComparison,
    "spatial-annotation-set": SpatialAnnotationSet,
    "structured-annotation-comparison": StructuredAnnotationComparison,
    "tool-identifier": ToolIdentifier,
    "validation-issue": ValidationIssue,
    "western-blot-structured-annotation": WesternBlotStructuredAnnotation,
}


def contract_schema(name: str) -> dict[str, Any]:
    """Build one deterministic validation schema from the canonical Pydantic type."""

    contract = CONTRACT_REGISTRY[name]
    generated = TypeAdapter(contract).json_schema(mode="validation")
    return {
        "$schema": JSON_SCHEMA_DIALECT,
        "$id": f"{SCHEMA_ID_PREFIX}{name}",
        **generated,
    }


def rendered_schema_snapshots() -> dict[str, str]:
    return {
        f"{name}.schema.json": json.dumps(contract_schema(name), indent=2, sort_keys=True) + "\n"
        for name in sorted(CONTRACT_REGISTRY)
    }
