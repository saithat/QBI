"""PostgreSQL review-queue projection and saved-filter repository."""

from __future__ import annotations

import json
from collections.abc import Sequence
from datetime import datetime
from typing import Any, NoReturn
from uuid import UUID

import psycopg
from hiveblot_contracts import (
    RegressionStatus,
    ReviewQueueCaseSummary,
    ReviewQueueFilters,
    ReviewStatus,
    SavedReviewView,
)
from psycopg import errors
from psycopg.rows import dict_row

from .errors import (
    ConcurrencyConflict,
    DuplicateEvaluationRecord,
    EvaluationNotFound,
)

QUEUE_CTES = ""

QUEUE_SELECT = """
SELECT
    evaluation.case_id,
    evaluation.case_key,
    evaluation.dataset_id,
    evaluation.assay_type,
    evaluation.review_status,
    evaluation.version AS case_version,
    source.source_label,
    source.artifact_id AS thumbnail_artifact_id,
    prediction.prediction_id,
    prediction.prediction_schema_version AS prediction_version,
    prediction.confidence,
    COALESCE(warnings.warning_count, 0)::INTEGER AS warning_count,
    (
        prediction.prediction_id IS NULL
        OR COALESCE(JSONB_ARRAY_LENGTH(prediction.evidence_json), 0) = 0
        OR COALESCE(warnings.missing_evidence, FALSE)
    ) AS missing_provenance,
    COALESCE(metadata.gold_eligible, FALSE) AS gold_eligible,
    COALESCE(prediction_totals.normalized_output_count, 0) > 1 AS model_disagreement,
    COALESCE(metadata.regression_status, 'not_evaluated') AS regression_status,
    COALESCE(head_errors.error_categories, ARRAY[]::TEXT[]) AS error_categories,
    COALESCE(assignments.reviewer_ids, ARRAY[]::UUID[]) AS active_reviewer_ids,
    COALESCE(assignments.exclusively_assigned, FALSE) AS exclusively_assigned,
    last_review.reviewer_id AS last_reviewer_id,
    last_review.updated_at AS last_reviewed_at,
    evaluation.updated_at,
    COUNT(*) OVER () AS total_count
FROM evaluation_cases AS evaluation
LEFT JOIN evaluation_case_review_metadata AS metadata ON metadata.case_id = evaluation.case_id
LEFT JOIN LATERAL (
    SELECT
        document.prediction_id,
        document.prediction_schema_version,
        document.confidence,
        document.evidence_json,
        document.validation_issues_json
    FROM prediction_documents AS document
    WHERE document.case_id = evaluation.case_id
    ORDER BY document.created_at DESC, document.prediction_id DESC
    LIMIT 1
) AS prediction ON TRUE
LEFT JOIN LATERAL (
    SELECT
        COUNT(DISTINCT document.normalized_output_json)
            FILTER (WHERE document.normalized_output_json IS NOT NULL) AS normalized_output_count
    FROM prediction_documents AS document
    WHERE document.case_id = evaluation.case_id
) AS prediction_totals ON TRUE
LEFT JOIN LATERAL (
    SELECT
        artifact.artifact_id,
        COALESCE(artifact.source_uri, artifact.original_filename) AS source_label
    FROM evaluation_case_artifacts AS case_artifact
    JOIN artifacts AS artifact ON artifact.artifact_id = case_artifact.artifact_id
    WHERE case_artifact.case_id = evaluation.case_id
    ORDER BY
        CASE case_artifact.artifact_role
            WHEN 'figure' THEN 0
            WHEN 'raw_source' THEN 1
            WHEN 'source_document' THEN 2
            ELSE 3
        END,
        artifact.created_at,
        artifact.artifact_id
    LIMIT 1
) AS source ON TRUE
LEFT JOIN LATERAL (
    SELECT document.reviewer_id, document.updated_at
    FROM annotation_documents AS document
    WHERE document.case_id = evaluation.case_id
    ORDER BY document.updated_at DESC, document.annotation_id DESC
    LIMIT 1
) AS last_review ON TRUE
LEFT JOIN LATERAL (
    SELECT
        ARRAY_AGG(assignment.reviewer_id ORDER BY assignment.reviewer_id) AS reviewer_ids,
        BOOL_OR(assignment.exclusive) AS exclusively_assigned
    FROM reviewer_assignments AS assignment
    WHERE assignment.case_id = evaluation.case_id
        AND assignment.assignment_status = 'assigned'
) AS assignments ON TRUE
LEFT JOIN LATERAL (
    SELECT ARRAY_AGG(DISTINCT code.category ORDER BY code.category) AS error_categories
    FROM annotation_documents AS document
    JOIN annotation_revision_error_codes AS revision_error
        ON revision_error.revision_id = document.head_revision_id
    JOIN annotation_error_codes AS code ON code.code = revision_error.error_code
    WHERE document.case_id = evaluation.case_id
) AS head_errors ON TRUE
LEFT JOIN LATERAL (
    SELECT
        COUNT(*) FILTER (
            WHERE issue ->> 'severity' IN ('warning', 'error')
        )::INTEGER AS warning_count,
        BOOL_OR(issue ->> 'code' = 'missing_evidence') AS missing_evidence
    FROM JSONB_ARRAY_ELEMENTS(
        COALESCE(prediction.validation_issues_json, '[]'::JSONB)
    ) AS issue
) AS warnings ON TRUE
"""


class PostgresReviewQueueRepository:
    def __init__(self, database_url: str) -> None:
        self._database_url = database_url

    def list_queue_cases(
        self,
        filters: ReviewQueueFilters,
        *,
        limit: int,
        offset: int,
    ) -> tuple[Sequence[ReviewQueueCaseSummary], int]:
        conditions, parameters = _filter_conditions(filters)
        where = " WHERE " + " AND ".join(conditions) if conditions else ""
        query = (
            QUEUE_CTES
            + QUEUE_SELECT
            + where
            + " ORDER BY evaluation.updated_at DESC, evaluation.case_id"
            + " LIMIT %(limit)s OFFSET %(offset)s"
        )
        parameters.update({"limit": limit, "offset": offset})
        with psycopg.connect(self._database_url, row_factory=dict_row) as connection:
            rows = connection.execute(query, parameters).fetchall()
            total = int(rows[0]["total_count"]) if rows else 0
            if not rows and offset:
                total = self._count_queue_cases(connection, filters)
        return tuple(_queue_case(row) for row in rows), total

    def _count_queue_cases(
        self,
        connection: psycopg.Connection[dict[str, Any]],
        filters: ReviewQueueFilters,
    ) -> int:
        conditions, parameters = _filter_conditions(filters)
        where = " WHERE " + " AND ".join(conditions) if conditions else ""
        query = "SELECT COUNT(*) AS total FROM (" + QUEUE_CTES + QUEUE_SELECT + where + ") queue"
        row = connection.execute(query, parameters).fetchone()
        return int(row["total"]) if row is not None else 0

    def create_saved_view(self, view: SavedReviewView) -> SavedReviewView:
        try:
            with psycopg.connect(self._database_url) as connection:
                connection.execute(
                    """
                    INSERT INTO saved_review_views (
                        view_id, owner_id, name, filters_json, version, created_at, updated_at
                    ) VALUES (%s, %s, %s, %s::jsonb, %s, %s, %s)
                    """,
                    (
                        view.view_id,
                        view.owner_id,
                        view.name,
                        view.filters.model_dump_json(),
                        view.version,
                        view.created_at,
                        view.updated_at,
                    ),
                )
        except errors.UniqueViolation as exc:
            raise DuplicateEvaluationRecord(
                f"saved view {view.name!r} already exists for this owner"
            ) from exc
        return view

    def get_saved_view(self, view_id: UUID) -> SavedReviewView | None:
        with psycopg.connect(self._database_url, row_factory=dict_row) as connection:
            row = connection.execute(
                "SELECT * FROM saved_review_views WHERE view_id = %s",
                (view_id,),
            ).fetchone()
        return _saved_view(row) if row is not None else None

    def list_saved_views(self, owner_id: UUID) -> Sequence[SavedReviewView]:
        with psycopg.connect(self._database_url, row_factory=dict_row) as connection:
            rows = connection.execute(
                """
                SELECT * FROM saved_review_views
                WHERE owner_id = %s
                ORDER BY updated_at DESC, view_id
                """,
                (owner_id,),
            ).fetchall()
        return tuple(_saved_view(row) for row in rows)

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
        try:
            with psycopg.connect(self._database_url, row_factory=dict_row) as connection:
                row = connection.execute(
                    """
                    UPDATE saved_review_views
                    SET name = %s,
                        filters_json = %s::jsonb,
                        version = version + 1,
                        updated_at = %s
                    WHERE view_id = %s AND owner_id = %s AND version = %s
                    RETURNING *
                    """,
                    (
                        name,
                        filters.model_dump_json(),
                        updated_at,
                        view_id,
                        owner_id,
                        expected_version,
                    ),
                ).fetchone()
                if row is None:
                    _raise_view_mutation_error(connection, view_id, owner_id)
        except (ConcurrencyConflict, EvaluationNotFound):
            raise
        except errors.UniqueViolation as exc:
            raise DuplicateEvaluationRecord(
                f"saved view {name!r} already exists for this owner"
            ) from exc
        return _saved_view(row)

    def delete_saved_view(
        self,
        view_id: UUID,
        *,
        owner_id: UUID,
        expected_version: int,
    ) -> None:
        with psycopg.connect(self._database_url, row_factory=dict_row) as connection:
            deleted = connection.execute(
                """
                DELETE FROM saved_review_views
                WHERE view_id = %s AND owner_id = %s AND version = %s
                RETURNING view_id
                """,
                (view_id, owner_id, expected_version),
            ).fetchone()
            if deleted is None:
                _raise_view_mutation_error(connection, view_id, owner_id)


def _filter_conditions(filters: ReviewQueueFilters) -> tuple[list[str], dict[str, object]]:
    conditions: list[str] = []
    parameters: dict[str, object] = {}
    if filters.review_statuses:
        conditions.append("evaluation.review_status = ANY(%(review_statuses)s)")
        parameters["review_statuses"] = [status.value for status in filters.review_statuses]
    if filters.dataset_id is not None:
        conditions.append("evaluation.dataset_id = %(dataset_id)s")
        parameters["dataset_id"] = filters.dataset_id
    if filters.assay_type is not None:
        conditions.append("evaluation.assay_type = %(assay_type)s")
        parameters["assay_type"] = filters.assay_type
    if filters.source_query is not None:
        conditions.append("source.source_label ILIKE %(source_query)s ESCAPE E'\\\\'")
        parameters["source_query"] = f"%{_escape_like(filters.source_query)}%"
    if filters.prediction_version is not None:
        conditions.append("prediction.prediction_schema_version = %(prediction_version)s")
        parameters["prediction_version"] = filters.prediction_version
    if filters.confidence_min is not None:
        conditions.append("prediction.confidence >= %(confidence_min)s")
        parameters["confidence_min"] = filters.confidence_min
    if filters.confidence_max is not None:
        conditions.append("prediction.confidence <= %(confidence_max)s")
        parameters["confidence_max"] = filters.confidence_max
    if filters.error_category is not None:
        conditions.append(
            "%(error_category)s = ANY(COALESCE(head_errors.error_categories, ARRAY[]::TEXT[]))"
        )
        parameters["error_category"] = filters.error_category
    if filters.reviewer_id is not None:
        conditions.append(
            """(
                EXISTS (
                    SELECT 1 FROM annotation_documents AS reviewer_document
                    WHERE reviewer_document.case_id = evaluation.case_id
                        AND reviewer_document.reviewer_id = %(reviewer_id)s
                )
                OR EXISTS (
                    SELECT 1 FROM reviewer_assignments AS reviewer_assignment
                    WHERE reviewer_assignment.case_id = evaluation.case_id
                        AND reviewer_assignment.reviewer_id = %(reviewer_id)s
                        AND reviewer_assignment.assignment_status = 'assigned'
                )
            )"""
        )
        parameters["reviewer_id"] = filters.reviewer_id
    if filters.missing_provenance is not None:
        conditions.append(
            """(
                prediction.prediction_id IS NULL
                OR COALESCE(JSONB_ARRAY_LENGTH(prediction.evidence_json), 0) = 0
                OR COALESCE(warnings.missing_evidence, FALSE)
            ) = %(missing_provenance)s"""
        )
        parameters["missing_provenance"] = filters.missing_provenance
    if filters.has_validation_warnings is not None:
        conditions.append("(COALESCE(warnings.warning_count, 0) > 0) = %(has_validation_warnings)s")
        parameters["has_validation_warnings"] = filters.has_validation_warnings
    if filters.gold_eligible is not None:
        conditions.append("COALESCE(metadata.gold_eligible, FALSE) = %(gold_eligible)s")
        parameters["gold_eligible"] = filters.gold_eligible
    if filters.model_disagreement is not None:
        conditions.append(
            "(COALESCE(prediction_totals.normalized_output_count, 0) > 1) = %(model_disagreement)s"
        )
        parameters["model_disagreement"] = filters.model_disagreement
    if filters.regression_status is not None:
        conditions.append(
            "COALESCE(metadata.regression_status, 'not_evaluated') = %(regression_status)s"
        )
        parameters["regression_status"] = filters.regression_status.value
    return conditions, parameters


def _queue_case(row: dict[str, Any]) -> ReviewQueueCaseSummary:
    return ReviewQueueCaseSummary(
        case_id=row["case_id"],
        case_key=row["case_key"],
        dataset_id=row["dataset_id"],
        assay_type=row["assay_type"],
        review_status=ReviewStatus(row["review_status"]),
        case_version=row["case_version"],
        source_label=row["source_label"],
        thumbnail_artifact_id=row["thumbnail_artifact_id"],
        prediction_id=row["prediction_id"],
        prediction_version=row["prediction_version"],
        confidence=row["confidence"],
        warning_count=row["warning_count"],
        missing_provenance=row["missing_provenance"],
        gold_eligible=row["gold_eligible"],
        model_disagreement=row["model_disagreement"],
        regression_status=RegressionStatus(row["regression_status"]),
        error_categories=tuple(row["error_categories"]),
        active_reviewer_ids=tuple(row["active_reviewer_ids"]),
        exclusively_assigned=row["exclusively_assigned"],
        last_reviewer_id=row["last_reviewer_id"],
        last_reviewed_at=row["last_reviewed_at"],
        updated_at=row["updated_at"],
    )


def _saved_view(row: dict[str, Any]) -> SavedReviewView:
    filters_json = json.dumps(row["filters_json"], separators=(",", ":"))
    return SavedReviewView(
        view_id=row["view_id"],
        owner_id=row["owner_id"],
        name=row["name"],
        filters=ReviewQueueFilters.model_validate_json(filters_json),
        version=row["version"],
        created_at=row["created_at"],
        updated_at=row["updated_at"],
    )


def _raise_view_mutation_error(
    connection: psycopg.Connection[dict[str, Any]],
    view_id: UUID,
    owner_id: UUID,
) -> NoReturn:
    row = connection.execute(
        "SELECT owner_id FROM saved_review_views WHERE view_id = %s",
        (view_id,),
    ).fetchone()
    if row is None or row["owner_id"] != owner_id:
        raise EvaluationNotFound(f"saved review view {view_id} does not exist")
    raise ConcurrencyConflict("saved review view version changed")


def _escape_like(value: str) -> str:
    return value.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
