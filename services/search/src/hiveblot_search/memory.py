"""Deterministic in-memory repository for tests and local composition."""

from __future__ import annotations

from collections.abc import Sequence
from datetime import datetime
from uuid import UUID

from hiveblot_contracts import (
    ArtifactVisibility,
    EvidenceIndexDocument,
    EvidenceSearchFilters,
    RetrievalEvaluationDatasetRecord,
    RetrievalEvaluationRunRecord,
    RetrievalMode,
    SearchIndexConfigurationRecord,
    SearchIndexStatus,
    SearchIndexVersionRecord,
)

from .embeddings import cosine_similarity, normalized_tokens
from .errors import (
    DuplicateEvidenceSearchRecord,
    EvidenceSearchNotFound,
    InvalidEvidenceSearchState,
)
from .repository import SearchCandidate, SearchCandidatePage
from .text import normalized_terms


class InMemoryEvidenceSearchRepository:
    def __init__(self) -> None:
        self.configurations: dict[UUID, SearchIndexConfigurationRecord] = {}
        self.versions: dict[UUID, SearchIndexVersionRecord] = {}
        self.documents: dict[
            UUID, dict[UUID, tuple[EvidenceIndexDocument, tuple[float, ...], str]]
        ] = {}
        self.datasets: dict[UUID, RetrievalEvaluationDatasetRecord] = {}
        self.evaluation_runs: dict[UUID, RetrievalEvaluationRunRecord] = {}

    def create_configuration(
        self, record: SearchIndexConfigurationRecord
    ) -> SearchIndexConfigurationRecord:
        if record.configuration_id in self.configurations or any(
            item.index_name == record.index_name
            and item.configuration_version == record.configuration_version
            for item in self.configurations.values()
        ):
            raise DuplicateEvidenceSearchRecord("search index configuration already exists")
        self.configurations[record.configuration_id] = record
        return record

    def get_configuration(self, configuration_id: UUID) -> SearchIndexConfigurationRecord | None:
        return self.configurations.get(configuration_id)

    def list_configurations(
        self, *, index_name: str | None, limit: int
    ) -> Sequence[SearchIndexConfigurationRecord]:
        values = [
            item
            for item in self.configurations.values()
            if index_name is None or item.index_name == index_name
        ]
        return tuple(
            sorted(
                values, key=lambda item: (item.created_at, str(item.configuration_id)), reverse=True
            )[:limit]
        )

    def start_version(self, record: SearchIndexVersionRecord) -> SearchIndexVersionRecord:
        if record.index_version_id in self.versions or any(
            item.index_name == record.index_name and item.index_version == record.index_version
            for item in self.versions.values()
        ):
            raise DuplicateEvidenceSearchRecord("search index version already exists")
        if record.configuration_id not in self.configurations:
            raise EvidenceSearchNotFound("search index configuration does not exist")
        self.versions[record.index_version_id] = record
        self.documents[record.index_version_id] = {}
        return record

    def complete_version(
        self,
        index_version_id: UUID,
        *,
        documents: Sequence[tuple[EvidenceIndexDocument, tuple[float, ...], str]],
        manifest_sha256: str,
        built_at: datetime,
    ) -> SearchIndexVersionRecord:
        current = self._version(index_version_id)
        if current.status is not SearchIndexStatus.BUILDING:
            raise InvalidEvidenceSearchState("only building index versions can be completed")
        document_ids = [item[0].document_id for item in documents]
        if len(document_ids) != len(set(document_ids)):
            raise DuplicateEvidenceSearchRecord("index document IDs must be unique")
        self.documents[index_version_id] = {
            document.document_id: (document, embedding, text)
            for document, embedding, text in documents
        }
        completed = current.model_copy(
            update={
                "status": SearchIndexStatus.READY,
                "document_count": len(documents),
                "manifest_sha256": manifest_sha256,
                "built_at": built_at,
            }
        )
        self.versions[index_version_id] = completed
        return completed

    def fail_version(
        self,
        index_version_id: UUID,
        *,
        reason: str,
    ) -> SearchIndexVersionRecord:
        current = self._version(index_version_id)
        if current.status is not SearchIndexStatus.BUILDING:
            raise InvalidEvidenceSearchState("only building index versions can fail")
        failed = current.model_copy(
            update={"status": SearchIndexStatus.FAILED, "failure_reason": reason}
        )
        self.versions[index_version_id] = failed
        return failed

    def get_version(self, index_version_id: UUID) -> SearchIndexVersionRecord | None:
        return self.versions.get(index_version_id)

    def list_versions(
        self, *, index_name: str | None, limit: int
    ) -> Sequence[SearchIndexVersionRecord]:
        values = [
            item
            for item in self.versions.values()
            if index_name is None or item.index_name == index_name
        ]
        return tuple(
            sorted(
                values, key=lambda item: (item.created_at, str(item.index_version_id)), reverse=True
            )[:limit]
        )

    def get_active_version(self, index_name: str) -> SearchIndexVersionRecord | None:
        return next(
            (
                item
                for item in self.versions.values()
                if item.index_name == index_name and item.status is SearchIndexStatus.ACTIVE
            ),
            None,
        )

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
    ) -> SearchCandidatePage:
        query_tokens = set(normalized_tokens(query))
        matches: list[tuple[float, SearchCandidate]] = []
        for document, embedding, text in self.documents.get(index_version_id, {}).values():
            if not _scope_allowed(document, allowed_organization_ids):
                continue
            if not _matches_filters(document, filters):
                continue
            document_tokens = set(normalized_tokens(text))
            lexical = (
                len(query_tokens & document_tokens) / len(query_tokens) if query_tokens else 0.0
            )
            candidate = SearchCandidate(
                document=document,
                embedding=embedding,
                lexical_score=lexical,
            )
            semantic = cosine_similarity(query_embedding, embedding)
            matches.append(
                (
                    _candidate_score(
                        retrieval_mode,
                        lexical=lexical,
                        semantic=semantic,
                        lexical_weight=lexical_weight,
                        semantic_weight=semantic_weight,
                    ),
                    candidate,
                )
            )
        matches.sort(
            key=lambda item: (item[0], str(item[1].document.document_id)),
            reverse=True,
        )
        return SearchCandidatePage(
            candidates=tuple(item[1] for item in matches[:limit]),
            total=len(matches),
        )

    def create_evaluation_dataset(
        self, record: RetrievalEvaluationDatasetRecord
    ) -> RetrievalEvaluationDatasetRecord:
        if record.dataset_id in self.datasets or any(
            item.dataset_name == record.dataset_name
            and item.dataset_version == record.dataset_version
            for item in self.datasets.values()
        ):
            raise DuplicateEvidenceSearchRecord("retrieval evaluation dataset already exists")
        self.datasets[record.dataset_id] = record
        return record

    def get_evaluation_dataset(self, dataset_id: UUID) -> RetrievalEvaluationDatasetRecord | None:
        return self.datasets.get(dataset_id)

    def create_evaluation_run(
        self, record: RetrievalEvaluationRunRecord
    ) -> RetrievalEvaluationRunRecord:
        if record.evaluation_run_id in self.evaluation_runs:
            raise DuplicateEvidenceSearchRecord("retrieval evaluation run already exists")
        if record.dataset_id not in self.datasets or record.index_version_id not in self.versions:
            raise EvidenceSearchNotFound("retrieval evaluation dependency does not exist")
        self.evaluation_runs[record.evaluation_run_id] = record
        return record

    def get_evaluation_run(self, evaluation_run_id: UUID) -> RetrievalEvaluationRunRecord | None:
        return self.evaluation_runs.get(evaluation_run_id)

    def has_passing_evaluation(self, index_version_id: UUID) -> bool:
        return any(
            item.index_version_id == index_version_id and item.passed
            for item in self.evaluation_runs.values()
        )

    def mark_evaluated(
        self, index_version_id: UUID, *, evaluated_at: datetime
    ) -> SearchIndexVersionRecord:
        current = self._version(index_version_id)
        if current.status not in {SearchIndexStatus.READY, SearchIndexStatus.ACTIVE}:
            raise InvalidEvidenceSearchState("only ready index versions can be evaluated")
        updated = current.model_copy(update={"evaluated_at": evaluated_at})
        self.versions[index_version_id] = updated
        return updated

    def activate_version(
        self, index_version_id: UUID, *, activated_at: datetime
    ) -> SearchIndexVersionRecord:
        current = self._version(index_version_id)
        if current.status is not SearchIndexStatus.READY:
            raise InvalidEvidenceSearchState("only ready index versions can be activated")
        passing = sorted(
            (
                item
                for item in self.evaluation_runs.values()
                if item.index_version_id == index_version_id and item.passed
            ),
            key=lambda item: (item.created_at, str(item.evaluation_run_id)),
            reverse=True,
        )
        if current.evaluated_at is None or not passing:
            raise InvalidEvidenceSearchState("index activation requires a passing evaluation")
        for version_id, version in tuple(self.versions.items()):
            if (
                version.index_name == current.index_name
                and version.status is SearchIndexStatus.ACTIVE
            ):
                self.versions[version_id] = version.model_copy(
                    update={"status": SearchIndexStatus.RETIRED}
                )
        activated = current.model_copy(
            update={
                "status": SearchIndexStatus.ACTIVE,
                "activated_at": activated_at,
                "activation_evaluation_run_id": passing[0].evaluation_run_id,
            }
        )
        self.versions[index_version_id] = activated
        return activated

    def _version(self, index_version_id: UUID) -> SearchIndexVersionRecord:
        value = self.versions.get(index_version_id)
        if value is None:
            raise EvidenceSearchNotFound(f"search index version {index_version_id} does not exist")
        return value


def _scope_allowed(
    document: EvidenceIndexDocument,
    allowed_organization_ids: tuple[UUID, ...] | None,
) -> bool:
    if allowed_organization_ids is None or document.visibility is ArtifactVisibility.PUBLIC:
        return True
    return document.organization_id in set(allowed_organization_ids)


def _matches_filters(
    document: EvidenceIndexDocument,
    filters: EvidenceSearchFilters,
) -> bool:
    terms = (
        (document.proteins, filters.proteins),
        (document.biological_systems, filters.biological_systems),
        (document.treatments, filters.treatments),
        (document.conditions, filters.conditions),
    )
    for actual, expected in terms:
        if expected and not set(normalized_terms(actual)) & set(normalized_terms(expected)):
            return False
    if filters.review_statuses and document.review_status not in filters.review_statuses:
        return False
    return not (
        filters.evidence_qualities and document.evidence_quality not in filters.evidence_qualities
    )


def _candidate_score(
    retrieval_mode: RetrievalMode,
    *,
    lexical: float,
    semantic: float,
    lexical_weight: float,
    semantic_weight: float,
) -> float:
    if retrieval_mode is RetrievalMode.LEXICAL:
        return lexical
    if retrieval_mode is RetrievalMode.SEMANTIC:
        return semantic
    return (lexical_weight * lexical + semantic_weight * semantic) / (
        lexical_weight + semantic_weight
    )
