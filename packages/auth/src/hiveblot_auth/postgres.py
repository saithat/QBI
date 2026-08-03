"""PostgreSQL identity, scope-resolution, token, and append-only audit mapping."""

from __future__ import annotations

from collections.abc import Sequence
from datetime import datetime
from typing import Any
from uuid import UUID

import psycopg
from hiveblot_contracts import (
    ArtifactVisibility,
    AuditEventRecord,
    AuditOutcome,
    AuthenticatedPrincipal,
    OrganizationMembership,
    OrganizationRecord,
    OrganizationRole,
    ResourceScope,
    UserRecord,
)
from psycopg.rows import dict_row

from .errors import InvalidAuthorizationState


class PostgresAuthorizationRepository:
    def __init__(self, database_url: str) -> None:
        self._database_url = database_url

    def authenticate_token(
        self,
        token_digest: str,
        *,
        authenticated_at: datetime,
    ) -> AuthenticatedPrincipal | None:
        with psycopg.connect(self._database_url, row_factory=dict_row) as connection:
            token = connection.execute(
                """
                SELECT token.token_id, token.user_id
                FROM api_access_tokens AS token
                JOIN auth_users AS auth_user ON auth_user.user_id = token.user_id
                WHERE token.token_digest = %s
                  AND token.revoked_at IS NULL
                  AND token.expires_at > %s
                  AND auth_user.user_status = 'active'
                FOR UPDATE OF token
                """,
                (token_digest, authenticated_at),
            ).fetchone()
            if token is None:
                return None
            connection.execute(
                "UPDATE api_access_tokens SET last_used_at = %s WHERE token_id = %s",
                (authenticated_at, token["token_id"]),
            )
            rows = connection.execute(
                """
                SELECT membership.*
                FROM organization_memberships AS membership
                JOIN organizations AS organization
                  ON organization.organization_id = membership.organization_id
                WHERE membership.user_id = %s
                  AND membership.active = TRUE
                  AND organization.organization_status = 'active'
                ORDER BY membership.organization_id
                """,
                (token["user_id"],),
            ).fetchall()
            return AuthenticatedPrincipal(
                user_id=token["user_id"],
                token_id=token["token_id"],
                memberships=tuple(_membership_from_row(row) for row in rows),
                authenticated_at=authenticated_at,
            )

    def resolve_scope(self, target_type: str, target_id: UUID) -> ResourceScope | None:
        with psycopg.connect(self._database_url, row_factory=dict_row) as connection:
            if target_type in {"golden_dataset", "golden_snapshot", "golden_export"}:
                table, column = {
                    "golden_dataset": ("golden_datasets", "dataset_id"),
                    "golden_snapshot": ("golden_dataset_snapshots", "snapshot_id"),
                    "golden_export": ("golden_dataset_exports", "export_id"),
                }[target_type]
                row = connection.execute(
                    f"SELECT 1 FROM {table} WHERE {column} = %s",  # noqa: S608 - fixed map
                    (target_id,),
                ).fetchone()
                return _public_scope() if row is not None else None
            query = _SCOPE_QUERIES.get(target_type)
            if query is None:
                raise InvalidAuthorizationState(f"unknown authorization target {target_type!r}")
            parameters = (target_id, target_id) if target_type == "pipeline_trace" else (target_id,)
            rows = connection.execute(query, parameters).fetchall()
        if not rows:
            return None
        scopes = {(row["visibility"], row["organization_id"]) for row in rows}
        if len(scopes) != 1:
            raise InvalidAuthorizationState(
                f"authorization target {target_type} {target_id} spans multiple scopes"
            )
        visibility, organization_id = next(iter(scopes))
        return ResourceScope(
            visibility=ArtifactVisibility(visibility),
            organization_id=organization_id,
        )

    def append_audit_event(self, event: AuditEventRecord) -> AuditEventRecord:
        with psycopg.connect(self._database_url) as connection:
            connection.execute(
                """
                INSERT INTO authorization_audit_events (
                    audit_event_id, actor_user_id, token_id, action, outcome,
                    target_type, target_id, organization_id, request_id, reason, occurred_at
                ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
                """,
                (
                    event.audit_event_id,
                    event.actor_user_id,
                    event.token_id,
                    event.action,
                    event.outcome.value,
                    event.target_type,
                    event.target_id,
                    event.organization_id,
                    event.request_id,
                    event.reason,
                    event.occurred_at,
                ),
            )
        return event

    def list_audit_events(
        self,
        *,
        organization_id: UUID,
        limit: int,
    ) -> Sequence[AuditEventRecord]:
        with psycopg.connect(self._database_url, row_factory=dict_row) as connection:
            rows = connection.execute(
                """
                SELECT * FROM authorization_audit_events
                WHERE organization_id = %s
                ORDER BY occurred_at DESC, audit_event_id DESC
                LIMIT %s
                """,
                (organization_id, limit),
            ).fetchall()
        return tuple(_audit_from_row(row) for row in rows)

    def create_user(self, record: UserRecord) -> UserRecord:
        try:
            with psycopg.connect(self._database_url) as connection:
                connection.execute(
                    """
                    INSERT INTO auth_users (
                        user_id, email, display_name, user_status, created_at, updated_at
                    ) VALUES (%s, %s, %s, %s, %s, %s)
                    """,
                    (
                        record.user_id,
                        record.email,
                        record.display_name,
                        record.status.value,
                        record.created_at,
                        record.updated_at,
                    ),
                )
        except psycopg.errors.UniqueViolation as exc:
            raise InvalidAuthorizationState("user email already exists") from exc
        return record

    def create_organization(
        self,
        organization: OrganizationRecord,
        administrator: OrganizationMembership,
    ) -> OrganizationRecord:
        try:
            with psycopg.connect(self._database_url) as connection:
                connection.execute(
                    """
                    INSERT INTO organizations (
                        organization_id, slug, display_name, organization_status,
                        created_at, updated_at
                    ) VALUES (%s, %s, %s, %s, %s, %s)
                    """,
                    (
                        organization.organization_id,
                        organization.slug,
                        organization.display_name,
                        organization.status.value,
                        organization.created_at,
                        organization.updated_at,
                    ),
                )
                _insert_membership(connection, administrator)
        except psycopg.errors.UniqueViolation as exc:
            raise InvalidAuthorizationState("organization slug already exists") from exc
        except psycopg.errors.ForeignKeyViolation as exc:
            raise InvalidAuthorizationState("organization administrator does not exist") from exc
        return organization

    def set_membership(self, membership: OrganizationMembership) -> OrganizationMembership:
        try:
            with psycopg.connect(self._database_url, row_factory=dict_row) as connection:
                row = connection.execute(
                    """
                    INSERT INTO organization_memberships (
                        membership_id, organization_id, user_id, role, active,
                        created_at, updated_at
                    ) VALUES (%s, %s, %s, %s, %s, %s, %s)
                    ON CONFLICT (organization_id, user_id) DO UPDATE SET
                        role = EXCLUDED.role,
                        active = EXCLUDED.active,
                        updated_at = EXCLUDED.updated_at
                    RETURNING *
                    """,
                    (
                        membership.membership_id,
                        membership.organization_id,
                        membership.user_id,
                        membership.role.value,
                        membership.active,
                        membership.created_at,
                        membership.updated_at,
                    ),
                ).fetchone()
        except psycopg.errors.ForeignKeyViolation as exc:
            raise InvalidAuthorizationState(
                "membership user or organization does not exist"
            ) from exc
        assert row is not None
        return _membership_from_row(row)


_SCOPE_QUERIES = {
    "artifact": "SELECT visibility, organization_id FROM artifacts WHERE artifact_id = %s",
    "artifact_upload": (
        "SELECT visibility, organization_id FROM artifact_uploads WHERE upload_id = %s"
    ),
    "evaluation_case": (
        "SELECT visibility, organization_id FROM evaluation_cases WHERE case_id = %s"
    ),
    "prediction": """
        SELECT evaluation_case.visibility, evaluation_case.organization_id
        FROM prediction_documents AS prediction
        JOIN evaluation_cases AS evaluation_case ON evaluation_case.case_id = prediction.case_id
        WHERE prediction.prediction_id = %s
    """,
    "annotation": (
        "SELECT visibility, organization_id FROM annotation_documents WHERE annotation_id = %s"
    ),
    "annotation_revision": """
        SELECT annotation.visibility, annotation.organization_id
        FROM annotation_revisions AS revision
        JOIN annotation_documents AS annotation ON annotation.annotation_id = revision.annotation_id
        WHERE revision.revision_id = %s
    """,
    "reviewer_assignment": """
        SELECT visibility, organization_id
        FROM reviewer_assignments AS assignment
        WHERE assignment.assignment_id = %s
    """,
    "adjudication": (
        "SELECT visibility, organization_id FROM adjudication_records WHERE adjudication_id = %s"
    ),
    "pipeline_run": """
        SELECT run.visibility, run.organization_id
        FROM pipeline_runs AS run
        WHERE run.run_id = %s
    """,
    "component_invocation": """
        SELECT run.visibility, run.organization_id
        FROM component_invocations AS invocation
        JOIN pipeline_runs AS run ON run.run_id = invocation.run_id
        WHERE invocation.invocation_id = %s
    """,
    "pipeline_trace": """
        SELECT run.visibility, run.organization_id
        FROM pipeline_runs AS run
        WHERE run.trace_id = %s
        UNION
        SELECT run.visibility, run.organization_id
        FROM component_invocations AS invocation
        JOIN pipeline_runs AS run ON run.run_id = invocation.run_id
        WHERE invocation.trace_id = %s
    """,
    "job": "SELECT visibility, organization_id FROM jobs WHERE job_id = %s",
    "job_attempt": """
        SELECT job.visibility, job.organization_id
        FROM job_attempts AS attempt
        JOIN jobs AS job ON job.job_id = attempt.job_id
        WHERE attempt.attempt_id = %s
    """,
}


def _public_scope() -> ResourceScope:
    return ResourceScope(visibility=ArtifactVisibility.PUBLIC)


def _membership_from_row(row: dict[str, Any]) -> OrganizationMembership:
    return OrganizationMembership(
        membership_id=row["membership_id"],
        organization_id=row["organization_id"],
        user_id=row["user_id"],
        role=OrganizationRole(row["role"]),
        active=row["active"],
        created_at=row["created_at"],
        updated_at=row["updated_at"],
    )


def _audit_from_row(row: dict[str, Any]) -> AuditEventRecord:
    return AuditEventRecord(
        audit_event_id=row["audit_event_id"],
        actor_user_id=row["actor_user_id"],
        token_id=row["token_id"],
        action=row["action"],
        outcome=AuditOutcome(row["outcome"]),
        target_type=row["target_type"],
        target_id=row["target_id"],
        organization_id=row["organization_id"],
        request_id=row["request_id"],
        reason=row["reason"],
        occurred_at=row["occurred_at"],
    )


def _insert_membership(connection: psycopg.Connection[Any], value: OrganizationMembership) -> None:
    connection.execute(
        """
        INSERT INTO organization_memberships (
            membership_id, organization_id, user_id, role, active, created_at, updated_at
        ) VALUES (%s, %s, %s, %s, %s, %s, %s)
        """,
        (
            value.membership_id,
            value.organization_id,
            value.user_id,
            value.role.value,
            value.active,
            value.created_at,
            value.updated_at,
        ),
    )
