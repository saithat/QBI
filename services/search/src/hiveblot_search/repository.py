"""Persistence protocol for versioned evidence indexes and retrieval evaluations."""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from datetime import datetime
from typing import Protocol
from uuid import UUID

from hiveblot_contracts import (
    EvidenceIndexDocument,
    EvidenceSearchFilters,
    RetrievalEvaluationDatasetRecord,
    RetrievalEvaluationRunRecord,
    RetrievalMode,
    SearchIndexConfigurationRecord,
    SearchIndexVersionRecord,
)


@dataclass(frozen=True, slots=True)
class SearchCandidate:
    document: EvidenceIndexDocument
    embedding: tuple[float, ...]
    lexical_score: float


@dataclass(frozen=True, slots=True)
class SearchCandidatePage:
    candidates: tuple[SearchCandidate, ...]
    total: int


class EvidenceSearchRepository(Protocol):
    def create_configuration(
        self, record: SearchIndexConfigurationRecord
    ) -> SearchIndexConfigurationRecord: ...

    def get_configuration(
        self, configuration_id: UUID
    ) -> SearchIndexConfigurationRecord | None: ...

    def list_configurations(
        self, *, index_name: str | None, limit: int
    ) -> Sequence[SearchIndexConfigurationRecord]: ...

    def start_version(self, record: SearchIndexVersionRecord) -> SearchIndexVersionRecord: ...

    def complete_version(
        self,
        index_version_id: UUID,
        *,
        documents: Sequence[tuple[EvidenceIndexDocument, tuple[float, ...], str]],
        manifest_sha256: str,
        built_at: datetime,
    ) -> SearchIndexVersionRecord: ...

    def fail_version(
        self,
        index_version_id: UUID,
        *,
        reason: str,
    ) -> SearchIndexVersionRecord: ...

    def get_version(self, index_version_id: UUID) -> SearchIndexVersionRecord | None: ...

    def list_versions(
        self, *, index_name: str | None, limit: int
    ) -> Sequence[SearchIndexVersionRecord]: ...

    def get_active_version(self, index_name: str) -> SearchIndexVersionRecord | None: ...

    def search_candidates(
        self,
        index_version_id: UUID,
        *,
        query: str,
        query_embedding: tuple[float, ...],
        retrieval_mode: RetrievalMode,
        lexical_weight: float,
        semantic_weight: float,
        filters: EvidenceSearchFilters,
        allowed_organization_ids: tuple[UUID, ...] | None,
        limit: int,
    ) -> SearchCandidatePage: ...

    def create_evaluation_dataset(
        self, record: RetrievalEvaluationDatasetRecord
    ) -> RetrievalEvaluationDatasetRecord: ...

    def get_evaluation_dataset(
        self, dataset_id: UUID
    ) -> RetrievalEvaluationDatasetRecord | None: ...

    def create_evaluation_run(
        self, record: RetrievalEvaluationRunRecord
    ) -> RetrievalEvaluationRunRecord: ...

    def get_evaluation_run(
        self, evaluation_run_id: UUID
    ) -> RetrievalEvaluationRunRecord | None: ...

    def has_passing_evaluation(self, index_version_id: UUID) -> bool: ...

    def mark_evaluated(
        self, index_version_id: UUID, *, evaluated_at: datetime
    ) -> SearchIndexVersionRecord: ...

    def activate_version(
        self, index_version_id: UUID, *, activated_at: datetime
    ) -> SearchIndexVersionRecord: ...
