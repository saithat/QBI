"""Evaluation references and validation diagnostics shared across components."""

from __future__ import annotations

from enum import StrEnum
from uuid import UUID

from pydantic import Field

from .base import ContractModel, Identifier


class ValidationSeverity(StrEnum):
    INFO = "info"
    WARNING = "warning"
    ERROR = "error"


class ValidationIssue(ContractModel):
    issue_id: UUID
    severity: ValidationSeverity
    code: Identifier
    message: str = Field(min_length=1, max_length=4000)
    field_path: str | None = Field(default=None, min_length=1, max_length=1000)
    evidence_artifact_ids: tuple[UUID, ...] = ()


class EvaluationCaseReference(ContractModel):
    case_id: UUID
    source_document_id: UUID
    dataset_name: Identifier | None = None
    dataset_version: Identifier | None = None
