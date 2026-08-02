"""Canonical, strict Pydantic v2 contracts for HiveBlot boundaries."""

from .artifacts import (
    ArtifactAcquisitionMethod,
    ArtifactRecord,
    ArtifactReference,
    ArtifactRelationship,
    ArtifactRelationshipKind,
    ArtifactVisibility,
    BoundingRegion,
    SourceDocumentKind,
    SourceDocumentRecord,
)
from .base import SCHEMA_VERSION, ContractModel, SchemaVersion
from .evaluation import EvaluationCaseReference, ValidationIssue, ValidationSeverity
from .identifiers import ModelIdentifier, PipelineIdentifier, ProducerIdentifier, ToolIdentifier
from .jobs import (
    CancelledJobResult,
    ContainerSpecification,
    ExpectedJobOutput,
    FailedJobResult,
    JobError,
    JobResult,
    JobSpecification,
    NamedArtifactReference,
    NetworkPolicy,
    ResourceRequirements,
    SucceededJobResult,
)

__all__ = [
    "SCHEMA_VERSION",
    "ArtifactAcquisitionMethod",
    "ArtifactRecord",
    "ArtifactReference",
    "ArtifactRelationship",
    "ArtifactRelationshipKind",
    "ArtifactVisibility",
    "BoundingRegion",
    "CancelledJobResult",
    "ContainerSpecification",
    "ContractModel",
    "EvaluationCaseReference",
    "ExpectedJobOutput",
    "FailedJobResult",
    "JobError",
    "JobResult",
    "JobSpecification",
    "ModelIdentifier",
    "NamedArtifactReference",
    "NetworkPolicy",
    "PipelineIdentifier",
    "ProducerIdentifier",
    "ResourceRequirements",
    "SchemaVersion",
    "SourceDocumentKind",
    "SourceDocumentRecord",
    "SucceededJobResult",
    "ToolIdentifier",
    "ValidationIssue",
    "ValidationSeverity",
]
