"""Versioned western-blot extraction orchestration."""

from .artifacts import ExtractionArtifactReader, VerifiedExtractionArtifactReader
from .assembly import assemble_extraction_result
from .errors import (
    ExtractionError,
    ExtractionImplementationNotFound,
    ExtractionOutputInvalid,
    UnsupportedExtractionSource,
)
from .implementations import (
    DetectionExecution,
    ExtractionImplementationRegistry,
    LegacyVlmExtractionImplementation,
    ModelExecution,
    RawCandidateModelResponse,
    WesternBlotExtractionImplementationAdapter,
)
from .normalization import normalize_candidate_predictions
from .service import WesternBlotExtractionService

__all__ = [
    "DetectionExecution",
    "ExtractionArtifactReader",
    "ExtractionError",
    "ExtractionImplementationNotFound",
    "ExtractionImplementationRegistry",
    "ExtractionOutputInvalid",
    "LegacyVlmExtractionImplementation",
    "ModelExecution",
    "RawCandidateModelResponse",
    "UnsupportedExtractionSource",
    "VerifiedExtractionArtifactReader",
    "WesternBlotExtractionImplementationAdapter",
    "WesternBlotExtractionService",
    "assemble_extraction_result",
    "normalize_candidate_predictions",
]
