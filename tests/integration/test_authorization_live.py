"""Opt-in PostgreSQL acceptance coverage for PRD-017 tenant integrity."""

from __future__ import annotations

import os
from datetime import UTC, datetime, timedelta
from uuid import UUID, uuid4

import psycopg
import pytest
from hiveblot_auth import AuthorizationService, PermissionDenied, PostgresAuthorizationRepository
from hiveblot_auth.bootstrap import bootstrap_identity
from hiveblot_contracts import (
    ArtifactVisibility,
    AuthorizationPermission,
    OrganizationRole,
    ResourceScope,
)
from psycopg.types.json import Jsonb

from hiveblot import db
from hiveblot.settings import Settings

pytestmark = pytest.mark.skipif(
    os.getenv("HIVEBLOT_RUN_LIVE_AUTH") != "1",
    reason="set HIVEBLOT_RUN_LIVE_AUTH=1 with an isolated PostgreSQL database",
)

NOW = datetime(2026, 8, 2, 19, tzinfo=UTC)
PEPPER = b"p" * 32


def test_live_authentication_audit_and_cross_tenant_database_constraints() -> None:
    settings = Settings(_env_file=None)
    db.initialize(settings.database_url)
    token_a = "hvb_live_" + "a" * 40
    token_b = "hvb_live_" + "b" * 40
    user_a, organization_a, _ = bootstrap_identity(
        settings.database_url,
        email=f"a-{uuid4()}@example.test",
        display_name="Organization A admin",
        organization_slug=f"org-a-{uuid4().hex[:12]}",
        organization_name="Organization A",
        role=OrganizationRole.ADMINISTRATOR.value,
        token_label="live-a",
        token=token_a,
        token_pepper=PEPPER,
        created_at=NOW,
        expires_at=NOW + timedelta(days=1),
    )
    user_b, organization_b, _ = bootstrap_identity(
        settings.database_url,
        email=f"b-{uuid4()}@example.test",
        display_name="Organization B admin",
        organization_slug=f"org-b-{uuid4().hex[:12]}",
        organization_name="Organization B",
        role=OrganizationRole.ADMINISTRATOR.value,
        token_label="live-b",
        token=token_b,
        token_pepper=PEPPER,
        created_at=NOW,
        expires_at=NOW + timedelta(days=1),
    )
    assert organization_a is not None
    assert organization_b is not None

    repository = PostgresAuthorizationRepository(settings.database_url)
    authorization = AuthorizationService(
        repository,
        token_pepper=PEPPER.decode(),
        clock=lambda: NOW,
    )
    principal_a = authorization.authenticate(token_a)
    principal_b = authorization.authenticate(token_b)
    private_a = ResourceScope(
        visibility=ArtifactVisibility.ORGANIZATION_PRIVATE,
        organization_id=organization_a,
    )
    authorization.authorize_scope(
        principal_a,
        AuthorizationPermission.ARTIFACT_WRITE,
        scope=private_a,
        target_type="artifact",
        request_id=uuid4(),
    )
    with pytest.raises(PermissionDenied):
        authorization.authorize_scope(
            principal_b,
            AuthorizationPermission.ARTIFACT_READ,
            scope=private_a,
            target_type="artifact",
            request_id=uuid4(),
        )
    events = authorization.list_audit_events(
        principal_a,
        organization_id=organization_a,
        request_id=uuid4(),
    )
    assert {event.outcome.value for event in events} == {"allowed", "denied"}

    public_artifact = uuid4()
    private_artifact_a = uuid4()
    private_artifact_b = uuid4()
    case_a = uuid4()
    public_case = uuid4()
    with psycopg.connect(settings.database_url) as connection:
        _insert_artifact(
            connection,
            public_artifact,
            sha256="1" * 64,
            visibility="public",
            organization_id=None,
        )
        _insert_artifact(
            connection,
            private_artifact_a,
            sha256="2" * 64,
            visibility="organization_private",
            organization_id=organization_a,
        )
        _insert_artifact(
            connection,
            private_artifact_b,
            sha256="3" * 64,
            visibility="organization_private",
            organization_id=organization_b,
        )
        _insert_case(connection, case_a, "organization_private", organization_a)
        _insert_case(connection, public_case, "public", None)
        connection.execute(
            """
            INSERT INTO evaluation_case_artifacts (
                case_id, artifact_id, artifact_role
            ) VALUES (%s, %s, 'figure')
            """,
            (case_a, public_artifact),
        )
        connection.execute(
            """
            INSERT INTO artifact_relationships (
                artifact_id, related_artifact_id, relationship_kind
            ) VALUES (%s, %s, 'derived_from')
            """,
            (private_artifact_a, public_artifact),
        )

    with (
        pytest.raises(psycopg.errors.RaiseException),
        psycopg.connect(settings.database_url) as connection,
    ):
        connection.execute(
            """
            INSERT INTO evaluation_case_artifacts (
                case_id, artifact_id, artifact_role
            ) VALUES (%s, %s, 'raw_source')
            """,
            (case_a, private_artifact_b),
        )
    with (
        pytest.raises(psycopg.errors.RaiseException),
        psycopg.connect(settings.database_url) as connection,
    ):
        connection.execute(
            """
            INSERT INTO evaluation_case_artifacts (
                case_id, artifact_id, artifact_role
            ) VALUES (%s, %s, 'figure')
            """,
            (public_case, private_artifact_a),
        )
    with (
        pytest.raises(psycopg.errors.RaiseException),
        psycopg.connect(settings.database_url) as connection,
    ):
        connection.execute(
            """
            INSERT INTO artifact_relationships (
                artifact_id, related_artifact_id, relationship_kind
            ) VALUES (%s, %s, 'related')
            """,
            (public_artifact, private_artifact_a),
        )

    _assert_job_constraints(
        settings.database_url,
        organization_a=organization_a,
        organization_b=organization_b,
        user_a=user_a,
        user_b=user_b,
        public_artifact=public_artifact,
        private_artifact_a=private_artifact_a,
        private_artifact_b=private_artifact_b,
    )
    _assert_pipeline_constraints(
        settings.database_url,
        case_a=case_a,
        public_case=public_case,
        organization_a=organization_a,
        public_artifact=public_artifact,
        private_artifact_a=private_artifact_a,
        private_artifact_b=private_artifact_b,
    )
    _assert_reviewer_assignment_constraints(
        settings.database_url,
        private_case=case_a,
        public_case=public_case,
        organization_a=organization_a,
        organization_b=organization_b,
        user_a=user_a,
        user_b=user_b,
    )

    with (
        pytest.raises(psycopg.errors.RaiseException),
        psycopg.connect(settings.database_url) as connection,
    ):
        connection.execute("UPDATE authorization_audit_events SET reason = 'changed'")

    assert principal_a.user_id == user_a
    assert principal_b.user_id == user_b


def _insert_artifact(
    connection: psycopg.Connection[tuple[object, ...]],
    artifact_id: UUID,
    *,
    sha256: str,
    visibility: str,
    organization_id: UUID | None,
) -> None:
    connection.execute(
        """
        INSERT INTO artifact_blobs (sha256, media_type, byte_size, storage_key)
        VALUES (%s, 'image/png', 10, %s)
        """,
        (sha256, f"acceptance/{sha256}"),
    )
    connection.execute(
        """
        INSERT INTO artifacts (
            artifact_id, blob_sha256, original_filename, acquisition_method,
            visibility, organization_id, created_at
        ) VALUES (%s, %s, %s, 'user_upload', %s, %s, %s)
        """,
        (artifact_id, sha256, f"{artifact_id}.png", visibility, organization_id, NOW),
    )


def _insert_case(
    connection: psycopg.Connection[tuple[object, ...]],
    case_id: UUID,
    visibility: str,
    organization_id: UUID | None,
) -> None:
    connection.execute(
        """
        INSERT INTO evaluation_cases (
            case_id, case_key, review_status, version, created_at, updated_at,
            visibility, organization_id
        ) VALUES (%s, %s, 'unreviewed', 1, %s, %s, %s, %s)
        """,
        (case_id, f"acceptance:{case_id}", NOW, NOW, visibility, organization_id),
    )


def _assert_job_constraints(
    database_url: str,
    *,
    organization_a: UUID,
    organization_b: UUID,
    user_a: UUID,
    user_b: UUID,
    public_artifact: UUID,
    private_artifact_a: UUID,
    private_artifact_b: UUID,
) -> None:
    job_id = uuid4()
    specification = {
        "visibility": "organization_private",
        "organization_id": str(organization_a),
        "inputs": [{"artifact": {"artifact_id": str(public_artifact)}}],
    }
    with psycopg.connect(database_url) as connection:
        _insert_job(connection, job_id, specification, organization_a, user_a)
        attempt_id = uuid4()
        connection.execute(
            """
            INSERT INTO job_attempts (
                attempt_id, job_id, attempt_number, worker_id, lease_token,
                status, executor_name, leased_at, lease_expires_at
            ) VALUES (%s, %s, 1, 'acceptance', %s, 'leased', 'local', %s, %s)
            """,
            (attempt_id, job_id, uuid4(), NOW, NOW + timedelta(minutes=5)),
        )
        connection.execute(
            """
            INSERT INTO job_outputs (job_id, output_name, attempt_id, artifact_id, created_at)
            VALUES (%s, 'result', %s, %s, %s)
            """,
            (job_id, attempt_id, private_artifact_a, NOW),
        )
    with (
        pytest.raises(psycopg.errors.RaiseException),
        psycopg.connect(database_url) as connection,
    ):
        _insert_job(
            connection,
            uuid4(),
            {
                "visibility": "organization_private",
                "organization_id": str(organization_a),
                "inputs": [{"artifact": {"artifact_id": str(private_artifact_b)}}],
            },
            organization_a,
            user_a,
        )
    with (
        pytest.raises(psycopg.errors.RaiseException),
        psycopg.connect(database_url) as connection,
    ):
        other_job = uuid4()
        _insert_job(connection, other_job, specification, organization_a, user_a)
        attempt_id = uuid4()
        connection.execute(
            """
            INSERT INTO job_attempts (
                attempt_id, job_id, attempt_number, worker_id, lease_token,
                status, executor_name, leased_at, lease_expires_at
            ) VALUES (%s, %s, 1, 'acceptance', %s, 'leased', 'local', %s, %s)
            """,
            (attempt_id, other_job, uuid4(), NOW, NOW + timedelta(minutes=5)),
        )
        connection.execute(
            """
            INSERT INTO job_outputs (
                job_id, output_name, attempt_id, artifact_id, created_at
            ) VALUES (%s, 'result', %s, %s, %s)
            """,
            (other_job, attempt_id, public_artifact, NOW),
        )
    parent_b = uuid4()
    with psycopg.connect(database_url) as connection:
        _insert_job(
            connection,
            parent_b,
            {
                "visibility": "organization_private",
                "organization_id": str(organization_b),
                "inputs": [{"artifact": {"artifact_id": str(private_artifact_b)}}],
            },
            organization_b,
            user_b,
        )
    with (
        pytest.raises(psycopg.errors.RaiseException),
        psycopg.connect(database_url) as connection,
    ):
        _insert_job(
            connection,
            uuid4(),
            {
                "visibility": "organization_private",
                "organization_id": str(organization_a),
                "parent_job_id": str(parent_b),
                "inputs": [{"artifact": {"artifact_id": str(public_artifact)}}],
            },
            organization_a,
            user_a,
        )


def _insert_job(
    connection: psycopg.Connection[tuple[object, ...]],
    job_id: UUID,
    specification: dict[str, object],
    organization_id: UUID,
    submitted_by: UUID,
) -> None:
    persisted_specification = {
        **specification,
        "submitted_by": str(submitted_by),
    }
    connection.execute(
        """
        INSERT INTO jobs (
            job_id, job_type, idempotency_key, trace_id, specification,
            specification_sha256, status, next_eligible_at, created_at, updated_at,
            visibility, organization_id, submitted_by
        ) VALUES (%s, 'acceptance', %s, %s, %s, %s, 'pending', %s, %s, %s, %s, %s, %s)
        """,
        (
            job_id,
            str(job_id),
            uuid4(),
            Jsonb(persisted_specification),
            "a" * 64,
            NOW,
            NOW,
            NOW,
            specification["visibility"],
            organization_id,
            submitted_by,
        ),
    )


def _assert_pipeline_constraints(
    database_url: str,
    *,
    case_a: UUID,
    public_case: UUID,
    organization_a: UUID,
    public_artifact: UUID,
    private_artifact_a: UUID,
    private_artifact_b: UUID,
) -> None:
    definition_id = uuid4()
    run_id = uuid4()
    invocation_id = uuid4()
    with psycopg.connect(database_url) as connection:
        connection.execute(
            """
            INSERT INTO pipeline_definitions (
                definition_id, pipeline_name, pipeline_version, definition_json, created_at
            ) VALUES (%s, %s, '1', '{}'::jsonb, %s)
            """,
            (definition_id, f"acceptance-{definition_id}", NOW),
        )
        connection.execute(
            """
            INSERT INTO pipeline_runs (
                run_id, case_id, definition_id, run_status, run_json,
                trace_id, visibility, organization_id, created_at, updated_at
            ) VALUES (
                %s, %s, %s, 'active', %s, %s,
                'organization_private', %s, %s, %s
            )
            """,
            (
                run_id,
                case_a,
                definition_id,
                Jsonb(
                    {
                        "visibility": "organization_private",
                        "organization_id": str(organization_a),
                    }
                ),
                uuid4(),
                organization_a,
                NOW,
                NOW,
            ),
        )
        connection.execute(
            """
            INSERT INTO pipeline_run_input_artifacts (run_id, position, artifact_id)
            VALUES (%s, 0, %s)
            """,
            (run_id, public_artifact),
        )
        connection.execute(
            """
            INSERT INTO component_invocations (
                invocation_id, run_id, component_key, invocation_status,
                invocation_json, trace_id, created_at
            ) VALUES (%s, %s, 'acceptance', 'pending', '{}'::jsonb, %s, %s)
            """,
            (invocation_id, run_id, uuid4(), NOW),
        )
        connection.execute(
            """
            INSERT INTO component_invocation_output_artifacts (
                invocation_id, position, artifact_id
            ) VALUES (%s, 0, %s)
            """,
            (invocation_id, private_artifact_a),
        )
        public_case_private_run_id = uuid4()
        connection.execute(
            """
            INSERT INTO pipeline_runs (
                run_id, case_id, definition_id, run_status, run_json,
                trace_id, visibility, organization_id, created_at, updated_at
            ) VALUES (
                %s, %s, %s, 'active', %s, %s,
                'organization_private', %s, %s, %s
            )
            """,
            (
                public_case_private_run_id,
                public_case,
                definition_id,
                Jsonb(
                    {
                        "visibility": "organization_private",
                        "organization_id": str(organization_a),
                    }
                ),
                uuid4(),
                organization_a,
                NOW,
                NOW,
            ),
        )
        connection.execute(
            """
            INSERT INTO pipeline_run_input_artifacts (run_id, position, artifact_id)
            VALUES (%s, 0, %s)
            """,
            (public_case_private_run_id, public_artifact),
        )
    with (
        pytest.raises(psycopg.errors.RaiseException),
        psycopg.connect(database_url) as connection,
    ):
        connection.execute(
            """
            INSERT INTO pipeline_run_input_artifacts (run_id, position, artifact_id)
            VALUES (%s, 1, %s)
            """,
            (run_id, private_artifact_b),
        )
    with (
        pytest.raises(psycopg.errors.RaiseException),
        psycopg.connect(database_url) as connection,
    ):
        connection.execute(
            """
            INSERT INTO component_invocation_output_artifacts (
                invocation_id, position, artifact_id
            ) VALUES (%s, 1, %s)
            """,
            (invocation_id, public_artifact),
        )


def _assert_reviewer_assignment_constraints(
    database_url: str,
    *,
    private_case: UUID,
    public_case: UUID,
    organization_a: UUID,
    organization_b: UUID,
    user_a: UUID,
    user_b: UUID,
) -> None:
    with psycopg.connect(database_url) as connection:
        _insert_assignment(connection, public_case, organization_a, user_a, exclusive=True)
        _insert_assignment(connection, public_case, organization_b, user_b, exclusive=True)

    with (
        pytest.raises(psycopg.errors.UniqueViolation),
        psycopg.connect(database_url) as connection,
    ):
        _insert_assignment(connection, public_case, organization_a, user_a, exclusive=True)

    with (
        pytest.raises(psycopg.errors.RaiseException),
        psycopg.connect(database_url) as connection,
    ):
        connection.execute(
            """
            INSERT INTO reviewer_assignments (
                assignment_id, case_id, reviewer_id, exclusive, assignment_status,
                version, assigned_at, updated_at, visibility, organization_id
            ) VALUES (%s, %s, %s, FALSE, 'assigned', 1, %s, %s, 'public', NULL)
            """,
            (uuid4(), private_case, uuid4(), NOW, NOW),
        )


def _insert_assignment(
    connection: psycopg.Connection[tuple[object, ...]],
    case_id: UUID,
    organization_id: UUID,
    reviewer_id: UUID,
    *,
    exclusive: bool,
) -> None:
    connection.execute(
        """
        INSERT INTO reviewer_assignments (
            assignment_id, case_id, reviewer_id, exclusive, assignment_status,
            version, assigned_at, updated_at, visibility, organization_id
        ) VALUES (
            %s, %s, %s, %s, 'assigned', 1, %s, %s,
            'organization_private', %s
        )
        """,
        (uuid4(), case_id, reviewer_id, exclusive, NOW, NOW, organization_id),
    )
