from __future__ import annotations

from datetime import datetime
from uuid import UUID

from hiveblot_contracts import (
    ReviewQueueCaseSummary,
    ReviewQueueFilters,
    SavedReviewView,
)
from hiveblot_evaluation import (
    ConcurrencyConflict,
    DuplicateEvaluationRecord,
    EvaluationNotFound,
)


class InMemoryReviewQueueRepository:
    def __init__(self, cases: tuple[ReviewQueueCaseSummary, ...] = ()) -> None:
        self.cases = cases
        self.views: dict[UUID, SavedReviewView] = {}
        self.last_filters: ReviewQueueFilters | None = None

    def list_queue_cases(
        self,
        filters: ReviewQueueFilters,
        *,
        limit: int,
        offset: int,
    ):
        self.last_filters = filters
        filtered = tuple(
            item
            for item in self.cases
            if (not filters.review_statuses or item.review_status in filters.review_statuses)
            and (
                filters.missing_provenance is None
                or item.missing_provenance is filters.missing_provenance
            )
        )
        return filtered[offset : offset + limit], len(filtered)

    def create_saved_view(self, view: SavedReviewView) -> SavedReviewView:
        if any(
            item.owner_id == view.owner_id and item.name == view.name
            for item in self.views.values()
        ):
            raise DuplicateEvaluationRecord(view.name)
        self.views[view.view_id] = view
        return view

    def get_saved_view(self, view_id: UUID) -> SavedReviewView | None:
        return self.views.get(view_id)

    def list_saved_views(self, owner_id: UUID):
        return tuple(item for item in self.views.values() if item.owner_id == owner_id)

    def update_saved_view(
        self,
        view_id: UUID,
        *,
        owner_id: UUID,
        expected_version: int,
        name: str,
        filters: ReviewQueueFilters,
        updated_at: datetime,
    ) -> SavedReviewView:
        current = self._owned_view(view_id, owner_id)
        if current.version != expected_version:
            raise ConcurrencyConflict("saved review view version changed")
        updated = current.model_copy(
            update={
                "name": name,
                "filters": filters,
                "version": current.version + 1,
                "updated_at": updated_at,
            }
        )
        self.views[view_id] = updated
        return updated

    def delete_saved_view(
        self,
        view_id: UUID,
        *,
        owner_id: UUID,
        expected_version: int,
    ) -> None:
        current = self._owned_view(view_id, owner_id)
        if current.version != expected_version:
            raise ConcurrencyConflict("saved review view version changed")
        del self.views[view_id]

    def _owned_view(self, view_id: UUID, owner_id: UUID) -> SavedReviewView:
        current = self.views.get(view_id)
        if current is None or current.owner_id != owner_id:
            raise EvaluationNotFound(str(view_id))
        return current
