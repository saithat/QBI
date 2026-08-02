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
from .identifiers import ModelIdentifier, PipelineIdentifier, ToolIdentifier
from .jobs import JobResult, JobSpecification

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
    "evaluation-case-reference": EvaluationCaseReference,
    "evaluation-case-record": EvaluationCaseRecord,
    "job-result": JobResult,
    "job-specification": JobSpecification,
    "model-identifier": ModelIdentifier,
    "pipeline-identifier": PipelineIdentifier,
    "prediction-document": PredictionDocument,
    "reviewer-assignment": ReviewerAssignment,
    "source-document-record": SourceDocumentRecord,
    "tool-identifier": ToolIdentifier,
    "validation-issue": ValidationIssue,
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
