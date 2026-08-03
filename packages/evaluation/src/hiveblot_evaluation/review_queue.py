"""Review-queue querying and saved-filter application service."""

from __future__ import annotations

from collections.abc import Callable, Sequence
from datetime import UTC, datetime
from typing import Protocol
from uuid import UUID, uuid4

from hiveblot_contracts import (
    ReviewQueueCaseSummary,
    ReviewQueueFilters,
    ReviewQueuePage,
    SavedReviewView,
)

from .errors import EvaluationNotFound, InvalidEvaluationState


class ReviewQueueRepository(Protocol):
    def list_queue_cases(
        self,
        filters: ReviewQueueFilters,
        *,
        accessible_organization_ids: tuple[UUID, ...] | None,
        limit: int,
        offset: int,
    ) -> tuple[Sequence[ReviewQueueCaseSummary], int]: ...

    def create_saved_view(self, view: SavedReviewView) -> SavedReviewView: ...

    def get_saved_view(self, view_id: UUID) -> SavedReviewView | None: ...

    def list_saved_views(self, owner_id: UUID) -> Sequence[SavedReviewView]: ...

    def update_saved_view(
        self,
        view_id: UUID,
        *,
        owner_id: UUID,
        expected_version: int,
        name: str,
        filters: ReviewQueueFilters,
        updated_at: datetime,
    ) -> SavedReviewView: ...

    def delete_saved_view(
        self,
        view_id: UUID,
        *,
        owner_id: UUID,
        expected_version: int,
    ) -> None: ...


class ReviewQueueService:
    def __init__(
        self,
        repository: ReviewQueueRepository,
        *,
        clock: Callable[[], datetime] | None = None,
    ) -> None:
        self._repository = repository
        self._clock = clock or (lambda: datetime.now(UTC))

    def browse(
        self,
        filters: ReviewQueueFilters,
        *,
        accessible_organization_ids: tuple[UUID, ...] | None = None,
        limit: int,
        offset: int,
    ) -> ReviewQueuePage:
        if not 1 <= limit <= 200:
            raise ValueError("limit must be between 1 and 200")
        if offset < 0:
            raise ValueError("offset must be non-negative")
        items, total = self._repository.list_queue_cases(
            filters,
            accessible_organization_ids=accessible_organization_ids,
            limit=limit,
            offset=offset,
        )
        next_offset = offset + len(items) if offset + len(items) < total else None
        return ReviewQueuePage(
            filters=filters,
            items=tuple(items),
            total=total,
            limit=limit,
            offset=offset,
            next_offset=next_offset,
        )

    def create_view(
        self,
        *,
        owner_id: UUID,
        name: str,
        filters: ReviewQueueFilters,
    ) -> SavedReviewView:
        normalized_name = name.strip()
        if not normalized_name:
            raise InvalidEvaluationState("saved review view name cannot be blank")
        now = self._clock()
        return self._repository.create_saved_view(
            SavedReviewView(
                view_id=uuid4(),
                owner_id=owner_id,
                name=normalized_name,
                filters=filters,
                version=1,
                created_at=now,
                updated_at=now,
            )
        )

    def get_view(self, view_id: UUID) -> SavedReviewView:
        view = self._repository.get_saved_view(view_id)
        if view is None:
            raise EvaluationNotFound(f"saved review view {view_id} does not exist")
        return view

    def list_views(self, owner_id: UUID) -> Sequence[SavedReviewView]:
        return self._repository.list_saved_views(owner_id)

    def update_view(
        self,
        view_id: UUID,
        *,
        owner_id: UUID,
        expected_version: int,
        name: str,
        filters: ReviewQueueFilters,
    ) -> SavedReviewView:
        normalized_name = name.strip()
        if not normalized_name:
            raise InvalidEvaluationState("saved review view name cannot be blank")
        return self._repository.update_saved_view(
            view_id,
            owner_id=owner_id,
            expected_version=expected_version,
            name=normalized_name,
            filters=filters,
            updated_at=self._clock(),
        )

    def delete_view(
        self,
        view_id: UUID,
        *,
        owner_id: UUID,
        expected_version: int,
    ) -> None:
        self._repository.delete_saved_view(
            view_id,
            owner_id=owner_id,
            expected_version=expected_version,
        )
