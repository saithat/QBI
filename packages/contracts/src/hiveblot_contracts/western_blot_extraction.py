"""Strict contracts for versioned western-blot extraction pipelines."""

from __future__ import annotations

from collections.abc import Hashable, Iterable
from enum import StrEnum
from typing import Self
from uuid import UUID

from pydantic import Field, StrictBool, StrictFloat, StrictInt, model_validator

from .artifacts import ArtifactReference, BoundingRegion
from .base import ContractModel, Identifier, Sha256Digest
from .evaluation import ValidationIssue
from .identifiers import ModelIdentifier, PipelineIdentifier, ToolIdentifier
from .spatial_editing import SpatialAnnotationSet
from .structured_annotations import WesternBlotStructuredAnnotation


class WesternBlotSourceKind(StrEnum):
    PDF = "pdf"
    IMAGE = "image"


class ModelConfidence(StrEnum):
    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"


class ModelBandState(StrEnum):
    PRESENT = "present"
    ABSENT = "absent"
    UNCERTAIN = "uncertain"


class WesternBlotExtractionConfiguration(ContractModel):
    implementation_name: Identifier
    dpi: StrictInt = Field(default=350, ge=72, le=1200)
    minimum_candidate_score: StrictFloat = Field(default=0.35, ge=0, le=1)
    minimum_model_score: StrictFloat = Field(default=0.65, ge=0, le=1)
    maximum_candidates: StrictInt = Field(default=50, ge=1, le=500)
    image_max_side: StrictInt = Field(default=1800, ge=128, le=16384)
    model_max_tokens: StrictInt = Field(default=4096, ge=1, le=65536)


class WesternBlotExtractionImplementation(ContractModel):
    implementation_name: Identifier
    implementation_version: Identifier
    pipeline: PipelineIdentifier
    detector: ToolIdentifier
    model: ModelIdentifier
    prompt_version: Identifier
    assembler: ToolIdentifier


class WesternBlotExtractionInput(ContractModel):
    source_artifact: ArtifactReference
    source_kind: WesternBlotSourceKind
    configuration: WesternBlotExtractionConfiguration
    trace_id: UUID


class WesternBlotFigureCandidate(ContractModel):
    candidate_id: UUID
    source_artifact: ArtifactReference
    region: BoundingRegion
    tight_region: BoundingRegion | None = None
    detector_score: StrictFloat = Field(ge=0, le=1)

    @model_validator(mode="after")
    def regions_match_the_source(self) -> Self:
        for region in (self.region, self.tight_region):
            if region is None:
                continue
            if region.source_artifact_id != self.source_artifact.artifact_id:
                raise ValueError("candidate regions must reference the candidate source artifact")
            if (
                region.page_number != self.region.page_number
                or region.canvas_width != self.region.canvas_width
                or region.canvas_height != self.region.canvas_height
            ):
                raise ValueError("candidate regions must share one source coordinate space")
        if self.tight_region is not None and not _contains(self.region, self.tight_region):
            raise ValueError("tight candidate geometry must fit inside the expanded region")
        return self


class WesternBlotFigureCandidateSet(ContractModel):
    source_artifact: ArtifactReference
    source_kind: WesternBlotSourceKind
    detector: ToolIdentifier
    candidates: tuple[WesternBlotFigureCandidate, ...]

    @model_validator(mode="after")
    def candidates_match_the_source(self) -> Self:
        _require_unique(
            (candidate.candidate_id for candidate in self.candidates),
            "western-blot figure candidate IDs",
        )
        if any(candidate.source_artifact != self.source_artifact for candidate in self.candidates):
            raise ValueError("figure candidates must use the candidate-set source artifact")
        return self


class WesternBlotTargetPrediction(ContractModel):
    target_id: UUID
    row_index: StrictInt = Field(ge=1)
    target: str = Field(min_length=1, max_length=1000)
    is_loading_control: StrictBool
    confidence: ModelConfidence


class WesternBlotLanePrediction(ContractModel):
    lane_id: UUID
    lane_index: StrictInt = Field(ge=1)
    condition: str | None = Field(default=None, min_length=1, max_length=2000)
    confidence: ModelConfidence


class WesternBlotBandPrediction(ContractModel):
    band_id: UUID
    row_index: StrictInt = Field(ge=1)
    target: str = Field(min_length=1, max_length=1000)
    lane_index: StrictInt = Field(ge=1)
    band_state: ModelBandState
    confidence: ModelConfidence


class WesternBlotPanelPrediction(ContractModel):
    panel_id: UUID
    panel_label: str | None = Field(default=None, min_length=1, max_length=500)
    panel_title: str | None = Field(default=None, min_length=1, max_length=1000)
    treatment_context: str | None = Field(default=None, min_length=1, max_length=4000)
    targets: tuple[WesternBlotTargetPrediction, ...] = Field(min_length=1)
    lanes: tuple[WesternBlotLanePrediction, ...] = Field(min_length=1)
    bands: tuple[WesternBlotBandPrediction, ...] = Field(min_length=1)
    warnings: tuple[str, ...] = ()

    @model_validator(mode="after")
    def panel_grid_is_complete(self) -> Self:
        _require_unique((item.target_id for item in self.targets), "target IDs")
        _require_unique((item.row_index for item in self.targets), "target row indices")
        _require_unique((item.lane_id for item in self.lanes), "lane IDs")
        _require_unique((item.lane_index for item in self.lanes), "lane indices")
        _require_unique((item.band_id for item in self.bands), "band IDs")
        band_keys = tuple((item.row_index, item.lane_index) for item in self.bands)
        _require_unique(band_keys, "band row/lane coordinates")
        targets = {item.row_index: item.target for item in self.targets}
        lanes = {item.lane_index for item in self.lanes}
        for band in self.bands:
            if band.row_index not in targets or band.lane_index not in lanes:
                raise ValueError("bands must reference rows and lanes from their panel")
            if band.target != targets[band.row_index]:
                raise ValueError("band target text must match its target row")
        expected = {(row, lane) for row in targets for lane in lanes}
        if set(band_keys) != expected:
            raise ValueError("every target-row and lane combination requires one band state")
        return self


class WesternBlotCandidatePrediction(ContractModel):
    candidate_id: UUID
    is_western_blot: StrictBool
    reason: str | None = Field(default=None, min_length=1, max_length=4000)
    figure_label: str | None = Field(default=None, min_length=1, max_length=1000)
    figure_caption: str | None = Field(default=None, min_length=1, max_length=20_000)
    biological_sample: str | None = Field(default=None, min_length=1, max_length=4000)
    cell_line_tissue: str | None = Field(default=None, min_length=1, max_length=4000)
    organism: str | None = Field(default=None, min_length=1, max_length=1000)
    sample_type: str | None = Field(default=None, min_length=1, max_length=1000)
    panels: tuple[WesternBlotPanelPrediction, ...] = ()
    warnings: tuple[str, ...] = ()
    raw_response_sha256: Sha256Digest

    @model_validator(mode="after")
    def classification_shape_is_valid(self) -> Self:
        _require_unique((panel.panel_id for panel in self.panels), "panel IDs")
        if self.is_western_blot and not self.panels:
            raise ValueError("positive western-blot predictions require at least one panel")
        if not self.is_western_blot:
            if self.panels:
                raise ValueError("negative western-blot predictions cannot contain panels")
            if self.reason is None:
                raise ValueError("negative western-blot predictions require a reason")
        return self


class WesternBlotCandidatePredictionSet(ContractModel):
    figure_candidates: WesternBlotFigureCandidateSet
    model: ModelIdentifier
    prompt_version: Identifier
    predictions: tuple[WesternBlotCandidatePrediction, ...]

    @model_validator(mode="after")
    def predictions_cover_candidates(self) -> Self:
        candidate_ids = {item.candidate_id for item in self.figure_candidates.candidates}
        prediction_ids = [item.candidate_id for item in self.predictions]
        _require_unique(prediction_ids, "candidate prediction IDs")
        if set(prediction_ids) != candidate_ids:
            raise ValueError("candidate predictions must cover every detected figure candidate")
        return self


class WesternBlotFieldEvidence(ContractModel):
    field_path: str = Field(pattern=r"^/", max_length=2000)
    region_ids: tuple[UUID, ...] = ()
    missing_reason: str | None = Field(default=None, min_length=1, max_length=2000)

    @model_validator(mode="after")
    def evidence_or_reason_is_required(self) -> Self:
        _require_unique(self.region_ids, "field evidence region IDs")
        if not self.region_ids and self.missing_reason is None:
            raise ValueError("field evidence requires a source region or a missing-evidence reason")
        return self


class WesternBlotExtractionResult(ContractModel):
    case_id: UUID
    source_artifact: ArtifactReference
    implementation: WesternBlotExtractionImplementation
    candidate_predictions: WesternBlotCandidatePredictionSet
    structured_annotation: WesternBlotStructuredAnnotation
    spatial_annotation_set: SpatialAnnotationSet
    field_evidence: tuple[WesternBlotFieldEvidence, ...]
    validation_issues: tuple[ValidationIssue, ...]
    confidence: StrictFloat | None = Field(default=None, ge=0, le=1)

    @model_validator(mode="after")
    def extraction_graph_is_consistent(self) -> Self:
        if self.candidate_predictions.figure_candidates.source_artifact != self.source_artifact:
            raise ValueError("extraction stages must retain the exact source artifact")
        region_ids = {
            item.spatial_annotation_id for item in self.spatial_annotation_set.spatial_annotations
        }
        if any(
            item.region.source_artifact_id != self.source_artifact.artifact_id
            for item in self.spatial_annotation_set.spatial_annotations
        ):
            raise ValueError("extraction geometry must reference the exact source artifact")
        structured_entities = (
            *self.structured_annotation.proteins,
            *self.structured_annotation.biological_contexts,
            *self.structured_annotation.treatments,
            *self.structured_annotation.lane_conditions,
            *self.structured_annotation.antibodies,
            *self.structured_annotation.molecular_weights,
            *self.structured_annotation.replicates,
        )
        if any(
            not set(entity.evidence_region_ids).issubset(region_ids)
            for entity in structured_entities
        ):
            raise ValueError("structured entity evidence must reference extraction geometry")
        if any(not set(item.region_ids).issubset(region_ids) for item in self.field_evidence):
            raise ValueError("field evidence must reference extraction geometry")
        _require_unique((item.field_path for item in self.field_evidence), "field evidence paths")
        _require_unique((item.issue_id for item in self.validation_issues), "validation issue IDs")
        allowed_artifacts = {self.source_artifact.artifact_id}
        if any(
            not set(issue.evidence_artifact_ids).issubset(allowed_artifacts)
            for issue in self.validation_issues
        ):
            raise ValueError("extraction issues must reference the source artifact")
        return self


class WesternBlotExtractionRunRecord(ContractModel):
    run_id: UUID
    definition_id: UUID
    case_id: UUID
    detector_invocation_id: UUID
    model_invocation_id: UUID
    assembler_invocation_id: UUID
    prediction_id: UUID
    publication_id: UUID
    trace_id: UUID
    pipeline: PipelineIdentifier
    result: WesternBlotExtractionResult

    @model_validator(mode="after")
    def identities_match_result(self) -> Self:
        if self.case_id != self.result.case_id:
            raise ValueError("extraction run and result case IDs must match")
        if self.pipeline != self.result.implementation.pipeline:
            raise ValueError("extraction run and implementation pipelines must match")
        invocation_ids = (
            self.detector_invocation_id,
            self.model_invocation_id,
            self.assembler_invocation_id,
        )
        _require_unique(invocation_ids, "extraction invocation IDs")
        return self


class WesternBlotComponentReplayRecord(ContractModel):
    run_id: UUID
    case_id: UUID
    invocation_id: UUID
    replay_of_invocation_id: UUID
    component_key: Identifier
    trace_id: UUID
    prediction_id: UUID | None = None
    publication_id: UUID | None = None


def _contains(parent: BoundingRegion, child: BoundingRegion) -> bool:
    return (
        child.x >= parent.x
        and child.y >= parent.y
        and child.x + child.width <= parent.x + parent.width
        and child.y + child.height <= parent.y + parent.height
    )


def _require_unique(values: Iterable[Hashable], label: str) -> None:
    materialized = tuple(values)
    if len(materialized) != len(set(materialized)):
        raise ValueError(f"{label} must be unique")
