"""HTTP adapters for review-queue browsing and saved views."""

from __future__ import annotations

from datetime import datetime
from typing import Annotated, Literal, Self
from uuid import UUID

from hiveblot_contracts import ContractModel
from pydantic import BeforeValidator, Field, model_validator


def _json_uuid(value: object) -> object:
    return UUID(value) if isinstance(value, str) else value


def _json_tuple(value: object) -> object:
    return tuple(value) if isinstance(value, list) else value


type JsonUUID = Annotated[UUID, BeforeValidator(_json_uuid)]
type JsonTuple[T] = Annotated[tuple[T, ...], BeforeValidator(_json_tuple)]
type ReviewStatusValue = Literal[
    "unreviewed", "in_review", "reviewed", "needs_adjudication", "adjudicated"
]
type RegressionStatusValue = Literal["not_evaluated", "stable", "improved", "regressed"]


class ReviewQueueFiltersInput(ContractModel):
    review_statuses: JsonTuple[ReviewStatusValue] = ()
    dataset_id: JsonUUID | None = None
    assay_type: Literal["western_blot"] | None = None
    source_query: str | None = Field(default=None, min_length=1, max_length=500)
    prediction_version: str | None = Field(default=None, min_length=1, max_length=200)
    confidence_min: float | None = Field(default=None, ge=0, le=1)
    confidence_max: float | None = Field(default=None, ge=0, le=1)
    error_category: str | None = Field(default=None, min_length=1, max_length=200)
    reviewer_id: JsonUUID | None = None
    missing_provenance: bool | None = None
    has_validation_warnings: bool | None = None
    gold_eligible: bool | None = None
    model_disagreement: bool | None = None
    regression_status: RegressionStatusValue | None = None

    @model_validator(mode="after")
    def filter_values_must_be_consistent(self) -> Self:
        if (
            self.confidence_min is not None
            and self.confidence_max is not None
            and self.confidence_min > self.confidence_max
        ):
            raise ValueError("confidence_min must be less than or equal to confidence_max")
        if len(self.review_statuses) != len(set(self.review_statuses)):
            raise ValueError("review_statuses must be unique")
        return self


class ReviewQueueCaseResponse(ContractModel):
    case_id: UUID
    case_key: str
    dataset_id: UUID | None
    assay_type: Literal["western_blot"]
    review_status: ReviewStatusValue
    case_version: int
    source_label: str | None
    thumbnail_artifact_id: UUID | None
    prediction_id: UUID | None
    prediction_version: str | None
    confidence: float | None
    warning_count: int
    missing_provenance: bool
    gold_eligible: bool
    model_disagreement: bool
    regression_status: RegressionStatusValue
    error_categories: tuple[str, ...]
    active_reviewer_ids: tuple[UUID, ...]
    exclusively_assigned: bool
    last_reviewer_id: UUID | None
    last_reviewed_at: datetime | None
    updated_at: datetime


class ReviewQueuePageResponse(ContractModel):
    filters: ReviewQueueFiltersInput
    items: tuple[ReviewQueueCaseResponse, ...]
    total: int
    limit: int
    offset: int
    next_offset: int | None


class CreateSavedReviewViewRequest(ContractModel):
    owner_id: JsonUUID
    name: str = Field(min_length=1, max_length=200)
    filters: ReviewQueueFiltersInput


class UpdateSavedReviewViewRequest(ContractModel):
    owner_id: JsonUUID
    expected_version: int = Field(ge=1)
    name: str = Field(min_length=1, max_length=200)
    filters: ReviewQueueFiltersInput


class SavedReviewViewResponse(ContractModel):
    view_id: UUID
    owner_id: UUID
    name: str
    filters: ReviewQueueFiltersInput
    version: int
    created_at: datetime
    updated_at: datetime


class SavedReviewViewListResponse(ContractModel):
    owner_id: UUID
    views: tuple[SavedReviewViewResponse, ...]
