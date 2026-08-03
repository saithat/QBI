"""PostgreSQL persistence and pre-ranking tenant filtering for evidence retrieval."""

from __future__ import annotations

import json
from collections.abc import Sequence
from datetime import datetime
from uuid import UUID

import psycopg
from hiveblot_contracts import (
    EvidenceIndexDocument,
    EvidenceSearchFilters,
    RetrievalEvaluationDatasetRecord,
    RetrievalEvaluationRunRecord,
    RetrievalMode,
    SearchIndexConfigurationRecord,
    SearchIndexStatus,
    SearchIndexVersionRecord,
)
from psycopg import errors
from psycopg.rows import dict_row
from psycopg.types.json import Jsonb

from .errors import (
    DuplicateEvidenceSearchRecord,
    EvidenceSearchNotFound,
    InvalidEvidenceSearchState,
)
from .repository import SearchCandidate, SearchCandidatePage
from .text import canonical_sha256, normalized_terms


class PostgresEvidenceSearchRepository:
    def __init__(self, database_url: str) -> None:
        self._database_url = database_url

    def create_configuration(
        self, record: SearchIndexConfigurationRecord
    ) -> SearchIndexConfigurationRecord:
        try:
            with psycopg.connect(self._database_url) as connection:
                connection.execute(
                    """
                    INSERT INTO evidence_index_configurations (
                        configuration_id, index_name, configuration_version,
                        embedding_provider, embedding_name, embedding_version,
                        embedding_dimensions, configuration_sha256, configuration_json,
                        created_by, created_at
                    ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
                    """,
                    (
                        record.configuration_id,
                        record.index_name,
                        record.configuration_version,
                        record.embedding_model.provider,
                        record.embedding_model.name,
                        record.embedding_model.version,
                        record.embedding_dimensions,
                        record.configuration_sha256,
                        Jsonb(record.model_dump(mode="json")),
                        record.created_by,
                        record.created_at,
                    ),
                )
        except errors.UniqueViolation as exc:
            raise DuplicateEvidenceSearchRecord(
                "search index configuration already exists"
            ) from exc
        except (errors.ForeignKeyViolation, errors.CheckViolation) as exc:
            raise InvalidEvidenceSearchState(str(exc)) from exc
        return record

    def get_configuration(self, configuration_id: UUID) -> SearchIndexConfigurationRecord | None:
        with psycopg.connect(self._database_url, row_factory=dict_row) as connection:
            row = connection.execute(
                """
                SELECT configuration_json FROM evidence_index_configurations
                WHERE configuration_id = %s
                """,
                (configuration_id,),
            ).fetchone()
        return _configuration(row["configuration_json"]) if row else None

    def list_configurations(
        self, *, index_name: str | None, limit: int
    ) -> Sequence[SearchIndexConfigurationRecord]:
        where = "WHERE index_name = %s" if index_name is not None else ""
        parameters: tuple[object, ...] = (index_name, limit) if index_name is not None else (limit,)
        with psycopg.connect(self._database_url, row_factory=dict_row) as connection:
            rows = connection.execute(
                f"""
                SELECT configuration_json FROM evidence_index_configurations
                {where}
                ORDER BY created_at DESC, configuration_id DESC
                LIMIT %s
                """,
                parameters,
            ).fetchall()
        return tuple(_configuration(row["configuration_json"]) for row in rows)

    def start_version(self, record: SearchIndexVersionRecord) -> SearchIndexVersionRecord:
        try:
            with psycopg.connect(self._database_url) as connection:
                connection.execute(
                    """
                    INSERT INTO evidence_index_versions (
                        index_version_id, configuration_id, index_name, index_version,
                        index_status, document_count, manifest_sha256, failure_reason,
                        created_by, created_at, built_at, evaluated_at, activated_at,
                        activation_evaluation_run_id
                    ) VALUES (
                        %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s
                    )
                    """,
                    _version_parameters(record),
                )
        except errors.UniqueViolation as exc:
            raise DuplicateEvidenceSearchRecord("search index version already exists") from exc
        except errors.ForeignKeyViolation as exc:
            raise EvidenceSearchNotFound(
                "search index configuration or actor does not exist"
            ) from exc
        except (errors.CheckViolation, errors.RaiseException) as exc:
            raise InvalidEvidenceSearchState(str(exc)) from exc
        return record

    def complete_version(
        self,
        index_version_id: UUID,
        *,
        documents: Sequence[tuple[EvidenceIndexDocument, tuple[float, ...], str]],
        manifest_sha256: str,
        built_at: datetime,
    ) -> SearchIndexVersionRecord:
        try:
            with psycopg.connect(self._database_url, row_factory=dict_row) as connection:
                current = connection.execute(
                    """
                    SELECT * FROM evidence_index_versions
                    WHERE index_version_id = %s FOR UPDATE
                    """,
                    (index_version_id,),
                ).fetchone()
                if current is None:
                    raise EvidenceSearchNotFound(
                        f"search index version {index_version_id} does not exist"
                    )
                if current["index_status"] != SearchIndexStatus.BUILDING.value:
                    raise InvalidEvidenceSearchState(
                        "only building index versions can be completed"
                    )
                for document, embedding, search_text in documents:
                    connection.execute(
                        """
                        INSERT INTO evidence_index_documents (
                            index_version_id, document_id, case_id,
                            source_annotation_revision_id, source_pipeline_publication_id,
                            paper_id, figure_label, experiment_label, protein_terms,
                            biological_system_terms, treatment_terms, condition_terms,
                            review_status, evidence_quality, visibility, organization_id,
                            search_text, embedding, document_sha256, document_json, created_at
                        ) VALUES (
                            %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s,
                            %s, %s, %s, %s, %s, %s, %s, %s, %s, %s
                        )
                        """,
                        (
                            index_version_id,
                            document.document_id,
                            document.case_id,
                            document.source_annotation_revision_id,
                            document.source_pipeline_publication_id,
                            document.paper_id,
                            document.figure_label,
                            document.experiment_label,
                            list(normalized_terms(document.proteins)),
                            list(normalized_terms(document.biological_systems)),
                            list(normalized_terms(document.treatments)),
                            list(normalized_terms(document.conditions)),
                            document.review_status.value,
                            document.evidence_quality.value,
                            document.visibility.value,
                            document.organization_id,
                            search_text,
                            list(embedding),
                            canonical_sha256(document.model_dump(mode="json")),
                            Jsonb(document.model_dump(mode="json")),
                            document.created_at,
                        ),
                    )
                    for position, citation in enumerate(document.citations):
                        connection.execute(
                            """
                            INSERT INTO evidence_index_document_citations (
                                index_version_id, document_id, artifact_id, citation_position
                            ) VALUES (%s, %s, %s, %s)
                            """,
                            (
                                index_version_id,
                                document.document_id,
                                citation.artifact.artifact_id,
                                position,
                            ),
                        )
                row = connection.execute(
                    """
                    UPDATE evidence_index_versions
                    SET index_status = 'ready', document_count = %s,
                        manifest_sha256 = %s, built_at = %s
                    WHERE index_version_id = %s
                    RETURNING *
                    """,
                    (len(documents), manifest_sha256, built_at, index_version_id),
                ).fetchone()
        except errors.UniqueViolation as exc:
            raise DuplicateEvidenceSearchRecord(
                "duplicate index document or manifest entry"
            ) from exc
        except errors.ForeignKeyViolation as exc:
            raise EvidenceSearchNotFound(
                "index document source or citation does not exist"
            ) from exc
        except (errors.CheckViolation, errors.RaiseException) as exc:
            raise InvalidEvidenceSearchState(str(exc)) from exc
        assert row is not None
        return _version(row)

    def fail_version(
        self,
        index_version_id: UUID,
        *,
        reason: str,
    ) -> SearchIndexVersionRecord:
        try:
            with psycopg.connect(self._database_url, row_factory=dict_row) as connection:
                row = connection.execute(
                    """
                    UPDATE evidence_index_versions
                    SET index_status = 'failed', failure_reason = %s
                    WHERE index_version_id = %s AND index_status = 'building'
                    RETURNING *
                    """,
                    (reason, index_version_id),
                ).fetchone()
        except (errors.CheckViolation, errors.RaiseException) as exc:
            raise InvalidEvidenceSearchState(str(exc)) from exc
        if row is None:
            raise EvidenceSearchNotFound(
                f"building search index version {index_version_id} does not exist"
            )
        return _version(row)

    def get_version(self, index_version_id: UUID) -> SearchIndexVersionRecord | None:
        with psycopg.connect(self._database_url, row_factory=dict_row) as connection:
            row = connection.execute(
                "SELECT * FROM evidence_index_versions WHERE index_version_id = %s",
                (index_version_id,),
            ).fetchone()
        return _version(row) if row else None

    def list_versions(
        self, *, index_name: str | None, limit: int
    ) -> Sequence[SearchIndexVersionRecord]:
        where = "WHERE index_name = %s" if index_name is not None else ""
        parameters: tuple[object, ...] = (index_name, limit) if index_name is not None else (limit,)
        with psycopg.connect(self._database_url, row_factory=dict_row) as connection:
            rows = connection.execute(
                f"""
                SELECT * FROM evidence_index_versions
                {where}
                ORDER BY created_at DESC, index_version_id DESC
                LIMIT %s
                """,
                parameters,
            ).fetchall()
        return tuple(_version(row) for row in rows)

    def get_active_version(self, index_name: str) -> SearchIndexVersionRecord | None:
        with psycopg.connect(self._database_url, row_factory=dict_row) as connection:
            row = connection.execute(
                """
                SELECT * FROM evidence_index_versions
                WHERE index_name = %s AND index_status = 'active'
                """,
                (index_name,),
            ).fetchone()
        return _version(row) if row else None

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
        clauses = ["index_version_id = %s"]
        parameters: list[object] = [index_version_id]
        if allowed_organization_ids is not None:
            if allowed_organization_ids:
                clauses.append("(visibility = 'public' OR organization_id = ANY(%s::uuid[]))")
                parameters.append(list(allowed_organization_ids))
            else:
                clauses.append("visibility = 'public'")
        for column, values in (
            ("protein_terms", filters.proteins),
            ("biological_system_terms", filters.biological_systems),
            ("treatment_terms", filters.treatments),
            ("condition_terms", filters.conditions),
        ):
            if values:
                clauses.append(f"{column} && %s::text[]")
                parameters.append(list(normalized_terms(values)))
        if filters.review_statuses:
            clauses.append("review_status = ANY(%s::text[])")
            parameters.append([item.value for item in filters.review_statuses])
        if filters.evidence_qualities:
            clauses.append("evidence_quality = ANY(%s::text[])")
            parameters.append([item.value for item in filters.evidence_qualities])
        where = " AND ".join(clauses)
        with psycopg.connect(self._database_url, row_factory=dict_row) as connection:
            total = connection.execute(
                f"SELECT COUNT(*) AS count FROM evidence_index_documents WHERE {where}",
                parameters,
            ).fetchone()
            total_weight = lexical_weight + semantic_weight
            rows = connection.execute(
                f"""
                WITH candidates AS (
                    SELECT document_json, embedding, document_id,
                           ts_rank_cd(
                               search_vector,
                               websearch_to_tsquery('english', %s),
                               32
                           ) AS lexical_score,
                           GREATEST(
                               0.0,
                               LEAST(
                                   1.0,
                                   semantic.semantic_score
                               )
                           ) AS semantic_score
                    FROM evidence_index_documents AS document
                    CROSS JOIN LATERAL (
                        SELECT COALESCE(SUM(document_value * query_value), 0.0)
                               AS semantic_score
                        FROM unnest(
                            document.embedding,
                            %s::double precision[]
                        ) AS component(document_value, query_value)
                    ) AS semantic
                    WHERE {where}
                )
                SELECT document_json, embedding, lexical_score
                FROM candidates
                ORDER BY CASE %s
                    WHEN 'lexical' THEN lexical_score
                    WHEN 'semantic' THEN semantic_score
                    ELSE (
                        %s * lexical_score + %s * semantic_score
                    ) / %s
                END DESC,
                document_id DESC
                LIMIT %s
                """,
                [
                    query,
                    list(query_embedding),
                    *parameters,
                    retrieval_mode.value,
                    lexical_weight,
                    semantic_weight,
                    total_weight,
                    limit,
                ],
            ).fetchall()
        assert total is not None
        return SearchCandidatePage(
            candidates=tuple(
                SearchCandidate(
                    document=_document(row["document_json"]),
                    embedding=tuple(float(value) for value in row["embedding"]),
                    lexical_score=max(0.0, min(1.0, float(row["lexical_score"]))),
                )
                for row in rows
            ),
            total=int(total["count"]),
        )

    def create_evaluation_dataset(
        self, record: RetrievalEvaluationDatasetRecord
    ) -> RetrievalEvaluationDatasetRecord:
        try:
            with psycopg.connect(self._database_url) as connection:
                connection.execute(
                    """
                    INSERT INTO retrieval_evaluation_datasets (
                        dataset_id, dataset_name, dataset_version, visibility, organization_id,
                        content_sha256, dataset_json, frozen_by, frozen_at
                    ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s)
                    """,
                    (
                        record.dataset_id,
                        record.dataset_name,
                        record.dataset_version,
                        record.visibility.value,
                        record.organization_id,
                        record.content_sha256,
                        Jsonb(record.model_dump(mode="json")),
                        record.frozen_by,
                        record.frozen_at,
                    ),
                )
        except errors.UniqueViolation as exc:
            raise DuplicateEvidenceSearchRecord(
                "retrieval evaluation dataset already exists"
            ) from exc
        except (errors.ForeignKeyViolation, errors.CheckViolation) as exc:
            raise InvalidEvidenceSearchState(str(exc)) from exc
        return record

    def get_evaluation_dataset(self, dataset_id: UUID) -> RetrievalEvaluationDatasetRecord | None:
        with psycopg.connect(self._database_url, row_factory=dict_row) as connection:
            row = connection.execute(
                """
                SELECT dataset_json FROM retrieval_evaluation_datasets
                WHERE dataset_id = %s
                """,
                (dataset_id,),
            ).fetchone()
        return _dataset(row["dataset_json"]) if row else None

    def create_evaluation_run(
        self, record: RetrievalEvaluationRunRecord
    ) -> RetrievalEvaluationRunRecord:
        try:
            with psycopg.connect(self._database_url) as connection:
                connection.execute(
                    """
                    INSERT INTO retrieval_evaluation_runs (
                        evaluation_run_id, dataset_id, dataset_sha256, index_version_id,
                        scorer_name, scorer_version, passed, run_json, created_at
                    ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s)
                    """,
                    (
                        record.evaluation_run_id,
                        record.dataset_id,
                        record.dataset_sha256,
                        record.index_version_id,
                        record.scorer.name,
                        record.scorer.version,
                        record.passed,
                        Jsonb(record.model_dump(mode="json")),
                        record.created_at,
                    ),
                )
        except errors.UniqueViolation as exc:
            raise DuplicateEvidenceSearchRecord("retrieval evaluation run already exists") from exc
        except errors.ForeignKeyViolation as exc:
            raise EvidenceSearchNotFound("retrieval evaluation dependency does not exist") from exc
        except (errors.CheckViolation, errors.RaiseException) as exc:
            raise InvalidEvidenceSearchState(str(exc)) from exc
        return record

    def get_evaluation_run(self, evaluation_run_id: UUID) -> RetrievalEvaluationRunRecord | None:
        with psycopg.connect(self._database_url, row_factory=dict_row) as connection:
            row = connection.execute(
                """
                SELECT run_json FROM retrieval_evaluation_runs
                WHERE evaluation_run_id = %s
                """,
                (evaluation_run_id,),
            ).fetchone()
        return _evaluation_run(row["run_json"]) if row else None

    def has_passing_evaluation(self, index_version_id: UUID) -> bool:
        with psycopg.connect(self._database_url) as connection:
            row = connection.execute(
                """
                SELECT EXISTS (
                    SELECT 1 FROM retrieval_evaluation_runs
                    WHERE index_version_id = %s AND passed = TRUE
                )
                """,
                (index_version_id,),
            ).fetchone()
        return bool(row and row[0])

    def mark_evaluated(
        self, index_version_id: UUID, *, evaluated_at: datetime
    ) -> SearchIndexVersionRecord:
        try:
            with psycopg.connect(self._database_url, row_factory=dict_row) as connection:
                row = connection.execute(
                    """
                    UPDATE evidence_index_versions SET evaluated_at = %s
                    WHERE index_version_id = %s AND index_status = 'ready'
                      AND evaluated_at IS NULL
                    RETURNING *
                    """,
                    (evaluated_at, index_version_id),
                ).fetchone()
        except (errors.CheckViolation, errors.RaiseException) as exc:
            raise InvalidEvidenceSearchState(str(exc)) from exc
        if row is None:
            raise InvalidEvidenceSearchState("ready index version could not be marked evaluated")
        return _version(row)

    def activate_version(
        self, index_version_id: UUID, *, activated_at: datetime
    ) -> SearchIndexVersionRecord:
        try:
            with psycopg.connect(self._database_url, row_factory=dict_row) as connection:
                target = connection.execute(
                    """
                    SELECT * FROM evidence_index_versions
                    WHERE index_version_id = %s FOR UPDATE
                    """,
                    (index_version_id,),
                ).fetchone()
                if target is None:
                    raise EvidenceSearchNotFound(
                        f"search index version {index_version_id} does not exist"
                    )
                if target["index_status"] != "ready":
                    raise InvalidEvidenceSearchState("only ready index versions can be activated")
                evaluation = connection.execute(
                    """
                    SELECT evaluation_run_id FROM retrieval_evaluation_runs
                    WHERE index_version_id = %s AND passed = TRUE
                    ORDER BY created_at DESC, evaluation_run_id DESC
                    LIMIT 1
                    """,
                    (index_version_id,),
                ).fetchone()
                if evaluation is None:
                    raise InvalidEvidenceSearchState(
                        "index activation requires a passing evaluation"
                    )
                connection.execute(
                    """
                    UPDATE evidence_index_versions SET index_status = 'retired'
                    WHERE index_name = %s AND index_status = 'active'
                    """,
                    (target["index_name"],),
                )
                row = connection.execute(
                    """
                    UPDATE evidence_index_versions
                    SET index_status = 'active', activated_at = %s,
                        activation_evaluation_run_id = %s
                    WHERE index_version_id = %s
                    RETURNING *
                    """,
                    (activated_at, evaluation["evaluation_run_id"], index_version_id),
                ).fetchone()
        except (errors.CheckViolation, errors.RaiseException, errors.UniqueViolation) as exc:
            raise InvalidEvidenceSearchState(str(exc)) from exc
        assert row is not None
        return _version(row)


def _version_parameters(record: SearchIndexVersionRecord) -> tuple[object, ...]:
    return (
        record.index_version_id,
        record.configuration_id,
        record.index_name,
        record.index_version,
        record.status.value,
        record.document_count,
        record.manifest_sha256,
        record.failure_reason,
        record.created_by,
        record.created_at,
        record.built_at,
        record.evaluated_at,
        record.activated_at,
        record.activation_evaluation_run_id,
    )


def _version(row: dict[str, object]) -> SearchIndexVersionRecord:
    return SearchIndexVersionRecord.model_validate(
        {
            "index_version_id": row["index_version_id"],
            "configuration_id": row["configuration_id"],
            "index_name": row["index_name"],
            "index_version": row["index_version"],
            "status": SearchIndexStatus(str(row["index_status"])),
            "document_count": row["document_count"],
            "manifest_sha256": row["manifest_sha256"],
            "failure_reason": row["failure_reason"],
            "created_by": row["created_by"],
            "created_at": row["created_at"],
            "built_at": row["built_at"],
            "evaluated_at": row["evaluated_at"],
            "activated_at": row["activated_at"],
            "activation_evaluation_run_id": row["activation_evaluation_run_id"],
        }
    )


def _configuration(value: object) -> SearchIndexConfigurationRecord:
    return SearchIndexConfigurationRecord.model_validate_json(json.dumps(value))


def _document(value: object) -> EvidenceIndexDocument:
    return EvidenceIndexDocument.model_validate_json(json.dumps(value))


def _dataset(value: object) -> RetrievalEvaluationDatasetRecord:
    return RetrievalEvaluationDatasetRecord.model_validate_json(json.dumps(value))


def _evaluation_run(value: object) -> RetrievalEvaluationRunRecord:
    return RetrievalEvaluationRunRecord.model_validate_json(json.dumps(value))
