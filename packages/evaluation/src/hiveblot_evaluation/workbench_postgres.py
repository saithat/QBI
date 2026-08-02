"""PostgreSQL mapping for case-associated caption and nearby-text context."""

from __future__ import annotations

from collections.abc import Sequence
from typing import Any
from uuid import UUID

import psycopg
from hiveblot_contracts import CaseArtifactRole, CaseSourceContext
from psycopg import errors
from psycopg.rows import dict_row

from .errors import ConcurrencyConflict, InvalidEvaluationState


class PostgresSourceContextRepository:
    def __init__(self, database_url: str) -> None:
        self._database_url = database_url

    def append_context(
        self,
        context: CaseSourceContext,
        *,
        expected_head_revision_id: UUID | None,
    ) -> CaseSourceContext:
        try:
            with psycopg.connect(self._database_url, row_factory=dict_row) as connection:
                association = connection.execute(
                    """
                    SELECT case_id FROM evaluation_case_artifacts
                    WHERE case_id = %s AND artifact_id = %s AND artifact_role = %s
                    FOR UPDATE
                    """,
                    (
                        context.case_id,
                        context.artifact_id,
                        context.artifact_role.value,
                    ),
                ).fetchone()
                if association is None:
                    raise InvalidEvaluationState(
                        "source context must reference a case artifact association"
                    )
                current = connection.execute(
                    """
                    SELECT context_revision_id, revision_number
                    FROM evaluation_case_source_contexts
                    WHERE case_id = %s AND artifact_id = %s AND artifact_role = %s
                    ORDER BY revision_number DESC
                    LIMIT 1
                    """,
                    (
                        context.case_id,
                        context.artifact_id,
                        context.artifact_role.value,
                    ),
                ).fetchone()
                current_head = current["context_revision_id"] if current is not None else None
                next_revision = current["revision_number"] + 1 if current is not None else 1
                if expected_head_revision_id != current_head:
                    raise ConcurrencyConflict("source context head changed")
                if (
                    context.prior_revision_id != current_head
                    or context.revision_number != next_revision
                ):
                    raise InvalidEvaluationState("source context revision chain is invalid")
                connection.execute(
                    """
                    INSERT INTO evaluation_case_source_contexts (
                        context_revision_id, case_id, artifact_id, artifact_role,
                        revision_number, prior_revision_id, caption, nearby_text, created_at
                    ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s)
                    """,
                    (
                        context.context_revision_id,
                        context.case_id,
                        context.artifact_id,
                        context.artifact_role.value,
                        context.revision_number,
                        context.prior_revision_id,
                        context.caption,
                        context.nearby_text,
                        context.created_at,
                    ),
                )
        except (ConcurrencyConflict, InvalidEvaluationState):
            raise
        except errors.UniqueViolation as exc:
            raise ConcurrencyConflict("source context head changed") from exc
        except errors.ForeignKeyViolation as exc:
            raise InvalidEvaluationState(
                "source context must reference a case artifact association"
            ) from exc
        except errors.CheckViolation as exc:
            raise InvalidEvaluationState("source context is invalid") from exc
        return context

    def list_contexts(self, case_id: UUID) -> Sequence[CaseSourceContext]:
        with psycopg.connect(self._database_url, row_factory=dict_row) as connection:
            rows = connection.execute(
                """
                SELECT DISTINCT ON (artifact_id, artifact_role) *
                FROM evaluation_case_source_contexts
                WHERE case_id = %s
                ORDER BY artifact_id, artifact_role, revision_number DESC
                """,
                (case_id,),
            ).fetchall()
        return tuple(_context_from_row(row) for row in rows)

    def list_context_revisions(
        self,
        case_id: UUID,
        artifact_id: UUID,
        artifact_role: CaseArtifactRole,
    ) -> Sequence[CaseSourceContext]:
        with psycopg.connect(self._database_url, row_factory=dict_row) as connection:
            rows = connection.execute(
                """
                SELECT * FROM evaluation_case_source_contexts
                WHERE case_id = %s AND artifact_id = %s AND artifact_role = %s
                ORDER BY revision_number
                """,
                (case_id, artifact_id, artifact_role.value),
            ).fetchall()
        return tuple(_context_from_row(row) for row in rows)


def _context_from_row(row: dict[str, Any]) -> CaseSourceContext:
    return CaseSourceContext(
        context_revision_id=row["context_revision_id"],
        case_id=row["case_id"],
        artifact_id=row["artifact_id"],
        artifact_role=CaseArtifactRole(row["artifact_role"]),
        revision_number=row["revision_number"],
        prior_revision_id=row["prior_revision_id"],
        caption=row["caption"],
        nearby_text=row["nearby_text"],
        created_at=row["created_at"],
    )
