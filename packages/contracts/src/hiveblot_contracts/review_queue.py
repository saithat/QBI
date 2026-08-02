"""Contracts for paginated evaluation browsing and saved review filters."""

from __future__ import annotations

from enum import StrEnum
from typing import Literal, Self
from uuid import UUID

from pydantic import AwareDatetime, Field, model_validator

from .annotations import ReviewStatus
from .base import ContractModel, Identifier


class RegressionStatus(StrEnum):
    NOT_EVALUATED = "not_evaluated"
    STABLE = "stable"
    IMPROVED = "improved"
    REGRESSED = "regressed"


class ReviewQueueFilters(ContractModel):
    review_statuses: tuple[ReviewStatus, ...] = ()
    dataset_id: UUID | None = None
    assay_type: Literal["western_blot"] | None = None
    source_query: str | None = Field(default=None, min_length=1, max_length=500)
    prediction_version: Identifier | None = None
    confidence_min: float | None = Field(default=None, ge=0, le=1)
    confidence_max: float | None = Field(default=None, ge=0, le=1)
    error_category: Identifier | None = None
    reviewer_id: UUID | None = None
    missing_provenance: bool | None = None
    has_validation_warnings: bool | None = None
    gold_eligible: bool | None = None
    model_disagreement: bool | None = None
    regression_status: RegressionStatus | None = None

    @model_validator(mode="after")
    def confidence_range_must_be_ordered(self) -> Self:
        if (
            self.confidence_min is not None
            and self.confidence_max is not None
            and self.confidence_min > self.confidence_max
        ):
            raise ValueError("confidence_min must be less than or equal to confidence_max")
        if len(self.review_statuses) != len(set(self.review_statuses)):
            raise ValueError("review_statuses must be unique")
        return self


class ReviewQueueCaseSummary(ContractModel):
    case_id: UUID
    case_key: Identifier
    dataset_id: UUID | None
    assay_type: Literal["western_blot"]
    review_status: ReviewStatus
    case_version: int = Field(ge=1)
    source_label: str | None
    thumbnail_artifact_id: UUID | None
    prediction_id: UUID | None
    prediction_version: str | None
    confidence: float | None = Field(default=None, ge=0, le=1)
    warning_count: int = Field(ge=0)
    missing_provenance: bool
    gold_eligible: bool
    model_disagreement: bool
    regression_status: RegressionStatus
    error_categories: tuple[Identifier, ...] = ()
    active_reviewer_ids: tuple[UUID, ...] = ()
    exclusively_assigned: bool
    last_reviewer_id: UUID | None
    last_reviewed_at: AwareDatetime | None
    updated_at: AwareDatetime


class ReviewQueuePage(ContractModel):
    filters: ReviewQueueFilters
    items: tuple[ReviewQueueCaseSummary, ...]
    total: int = Field(ge=0)
    limit: int = Field(ge=1, le=200)
    offset: int = Field(ge=0)
    next_offset: int | None = Field(default=None, ge=0)


class SavedReviewView(ContractModel):
    view_id: UUID
    owner_id: UUID
    name: str = Field(min_length=1, max_length=200)
    filters: ReviewQueueFilters
    version: int = Field(ge=1)
    created_at: AwareDatetime
    updated_at: AwareDatetime
