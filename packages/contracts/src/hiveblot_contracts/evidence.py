"""Source evidence workbench contracts."""

from __future__ import annotations

from typing import Annotated, Literal, Self
from uuid import UUID

from pydantic import AwareDatetime, Field, model_validator

from .annotations import CaseArtifactRole
from .artifacts import ArtifactRecord, BoundingRegion
from .base import ContractModel


class CaseSourceContext(ContractModel):
    context_revision_id: UUID
    case_id: UUID
    artifact_id: UUID
    artifact_role: CaseArtifactRole
    revision_number: int = Field(ge=1)
    prior_revision_id: UUID | None = None
    caption: str | None = Field(default=None, max_length=50_000)
    nearby_text: str | None = Field(default=None, max_length=100_000)
    created_at: AwareDatetime

    @model_validator(mode="after")
    def revision_chain_must_be_consistent(self) -> Self:
        if self.revision_number == 1 and self.prior_revision_id is not None:
            raise ValueError("initial source context cannot have a prior revision")
        if self.revision_number > 1 and self.prior_revision_id is None:
            raise ValueError("later source context requires a prior revision")
        if not (self.caption and self.caption.strip()) and not (
            self.nearby_text and self.nearby_text.strip()
        ):
            raise ValueError("source context requires a caption or nearby text")
        return self


class WorkbenchEvidenceSource(ContractModel):
    artifact: ArtifactRecord
    artifact_role: CaseArtifactRole
    page_number: int | None = Field(default=None, ge=1)
    caption: str | None = Field(default=None, max_length=50_000)
    nearby_text: str | None = Field(default=None, max_length=100_000)


class EvidenceOverlayBase(ContractModel):
    overlay_id: UUID
    source_artifact_id: UUID
    region: BoundingRegion
    label: str | None = Field(default=None, max_length=1000)
    linked_field_keys: tuple[str, ...] = ()


class PredictionEvidenceOverlay(EvidenceOverlayBase):
    overlay_source: Literal["prediction"] = "prediction"
    prediction_id: UUID


class ReviewerEvidenceOverlay(EvidenceOverlayBase):
    overlay_source: Literal["reviewer"] = "reviewer"
    annotation_id: UUID
    revision_id: UUID
    reviewer_id: UUID


class AdjudicationEvidenceOverlay(EvidenceOverlayBase):
    overlay_source: Literal["adjudication"] = "adjudication"
    adjudication_id: UUID
    revision_id: UUID


type EvidenceOverlay = Annotated[
    PredictionEvidenceOverlay | ReviewerEvidenceOverlay | AdjudicationEvidenceOverlay,
    Field(discriminator="overlay_source"),
]


class SourceEvidenceWorkbench(ContractModel):
    case_id: UUID
    case_version: int = Field(ge=1)
    sources: tuple[WorkbenchEvidenceSource, ...]
    overlays: tuple[EvidenceOverlay, ...]
