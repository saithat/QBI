"""PostgreSQL mapping for immutable pipeline registry records."""

from __future__ import annotations

import json
from collections.abc import Sequence
from typing import Any
from uuid import UUID

import psycopg
from hiveblot_contracts import (
    ComponentInvocationRecord,
    PipelineDefinitionRecord,
    PipelineIdentifier,
    PipelinePublicationRecord,
    PipelineRunRecord,
)
from psycopg import errors
from psycopg.rows import dict_row
from psycopg.types.json import Jsonb

from .errors import (
    ConcurrencyConflict,
    DuplicateEvaluationRecord,
    EvaluationNotFound,
    InvalidEvaluationState,
)


class PostgresPipelineRunRepository:
    def __init__(self, database_url: str) -> None:
        self._database_url = database_url

    def create_definition(
        self,
        definition: PipelineDefinitionRecord,
    ) -> PipelineDefinitionRecord:
        try:
            with psycopg.connect(self._database_url) as connection:
                connection.execute(
                    """
                    INSERT INTO pipeline_definitions (
                        definition_id, pipeline_name, pipeline_version,
                        definition_json, created_at
                    ) VALUES (%s, %s, %s, %s, %s)
                    """,
                    (
                        definition.definition_id,
                        definition.pipeline.name,
                        definition.pipeline.version,
                        Jsonb(definition.model_dump(mode="json")),
                        definition.created_at,
                    ),
                )
        except errors.UniqueViolation as exc:
            raise DuplicateEvaluationRecord(
                f"pipeline {definition.pipeline.name}@{definition.pipeline.version} already exists"
            ) from exc
        return definition

    def get_definition(self, definition_id: UUID) -> PipelineDefinitionRecord | None:
        with psycopg.connect(self._database_url, row_factory=dict_row) as connection:
            row = connection.execute(
                "SELECT definition_json FROM pipeline_definitions WHERE definition_id = %s",
                (definition_id,),
            ).fetchone()
        return _model(PipelineDefinitionRecord, row["definition_json"]) if row else None

    def get_definition_by_pipeline(
        self,
        pipeline: PipelineIdentifier,
    ) -> PipelineDefinitionRecord | None:
        with psycopg.connect(self._database_url, row_factory=dict_row) as connection:
            row = connection.execute(
                """
                SELECT definition_json FROM pipeline_definitions
                WHERE pipeline_name = %s AND pipeline_version = %s
                """,
                (pipeline.name, pipeline.version),
            ).fetchone()
        return _model(PipelineDefinitionRecord, row["definition_json"]) if row else None

    def list_definitions(self) -> Sequence[PipelineDefinitionRecord]:
        with psycopg.connect(self._database_url, row_factory=dict_row) as connection:
            rows = connection.execute(
                """
                SELECT definition_json FROM pipeline_definitions
                ORDER BY pipeline_name, pipeline_version, definition_id
                """
            ).fetchall()
        return tuple(_model(PipelineDefinitionRecord, row["definition_json"]) for row in rows)

    def create_run(
        self,
        run: PipelineRunRecord,
        invocations: tuple[ComponentInvocationRecord, ...],
    ) -> PipelineRunRecord:
        try:
            with psycopg.connect(self._database_url) as connection:
                connection.execute(
                    """
                    INSERT INTO pipeline_runs (
                        run_id, case_id, definition_id, run_status, run_json,
                        trace_id, created_at, updated_at
                    ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s)
                    """,
                    (
                        run.run_id,
                        run.case_id,
                        run.definition_id,
                        run.status.value,
                        Jsonb(run.model_dump(mode="json")),
                        run.trace_id,
                        run.created_at,
                        run.updated_at,
                    ),
                )
                _insert_artifact_rows(
                    connection,
                    "pipeline_run_input_artifacts",
                    "run_id",
                    run.run_id,
                    run.input_artifacts,
                )
                for invocation in invocations:
                    _insert_invocation(connection, invocation, include_parents=False)
                for invocation in invocations:
                    _insert_parent_rows(connection, invocation)
        except errors.UniqueViolation as exc:
            raise DuplicateEvaluationRecord(f"pipeline run {run.run_id} already exists") from exc
        except errors.ForeignKeyViolation as exc:
            raise InvalidEvaluationState(
                "pipeline run references an unknown case, definition, artifact, or parent"
            ) from exc
        return run

    def get_run(self, run_id: UUID) -> PipelineRunRecord | None:
        with psycopg.connect(self._database_url, row_factory=dict_row) as connection:
            row = connection.execute(
                "SELECT run_json FROM pipeline_runs WHERE run_id = %s",
                (run_id,),
            ).fetchone()
        return _model(PipelineRunRecord, row["run_json"]) if row else None

    def list_runs(self, case_id: UUID) -> Sequence[PipelineRunRecord]:
        with psycopg.connect(self._database_url, row_factory=dict_row) as connection:
            rows = connection.execute(
                """
                SELECT run_json FROM pipeline_runs
                WHERE case_id = %s ORDER BY created_at, run_id
                """,
                (case_id,),
            ).fetchall()
        return tuple(_model(PipelineRunRecord, row["run_json"]) for row in rows)

    def get_invocation(self, invocation_id: UUID) -> ComponentInvocationRecord | None:
        with psycopg.connect(self._database_url, row_factory=dict_row) as connection:
            row = connection.execute(
                "SELECT invocation_json FROM component_invocations WHERE invocation_id = %s",
                (invocation_id,),
            ).fetchone()
        return _model(ComponentInvocationRecord, row["invocation_json"]) if row else None

    def list_invocations(self, run_id: UUID) -> Sequence[ComponentInvocationRecord]:
        with psycopg.connect(self._database_url, row_factory=dict_row) as connection:
            rows = connection.execute(
                """
                SELECT invocation_json FROM component_invocations
                WHERE run_id = %s ORDER BY created_at, invocation_id
                """,
                (run_id,),
            ).fetchall()
        return tuple(_model(ComponentInvocationRecord, row["invocation_json"]) for row in rows)

    def complete_invocation(
        self,
        invocation: ComponentInvocationRecord,
    ) -> ComponentInvocationRecord:
        assert invocation.result is not None
        try:
            with psycopg.connect(self._database_url) as connection:
                row = connection.execute(
                    """
                    UPDATE component_invocations
                    SET invocation_status = %s, invocation_json = %s, completed_at = %s
                    WHERE invocation_id = %s AND invocation_status = 'pending'
                    RETURNING invocation_id
                    """,
                    (
                        invocation.status.value,
                        Jsonb(invocation.model_dump(mode="json")),
                        invocation.result.completed_at,
                        invocation.invocation_id,
                    ),
                ).fetchone()
                if row is None:
                    exists = connection.execute(
                        "SELECT 1 FROM component_invocations WHERE invocation_id = %s",
                        (invocation.invocation_id,),
                    ).fetchone()
                    if exists is None:
                        raise EvaluationNotFound(
                            f"component invocation {invocation.invocation_id} does not exist"
                        )
                    raise ConcurrencyConflict("component invocation already has a terminal result")
                _insert_artifact_rows(
                    connection,
                    "component_invocation_output_artifacts",
                    "invocation_id",
                    invocation.invocation_id,
                    invocation.result.output_artifacts,
                )
                for artifact_id in sorted(
                    {item.artifact.artifact_id for item in invocation.result.evidence},
                    key=str,
                ):
                    connection.execute(
                        """
                        INSERT INTO component_invocation_evidence_artifacts (
                            invocation_id, artifact_id
                        ) VALUES (%s, %s)
                        """,
                        (invocation.invocation_id, artifact_id),
                    )
        except errors.ForeignKeyViolation as exc:
            raise InvalidEvaluationState("component result references an unknown artifact") from exc
        return invocation

    def create_replay(
        self,
        invocation: ComponentInvocationRecord,
    ) -> ComponentInvocationRecord:
        try:
            with psycopg.connect(self._database_url) as connection:
                _insert_invocation(connection, invocation, include_parents=True)
        except errors.UniqueViolation as exc:
            raise DuplicateEvaluationRecord(
                f"component invocation {invocation.invocation_id} already exists"
            ) from exc
        except errors.ForeignKeyViolation as exc:
            raise InvalidEvaluationState(
                "component replay references an unknown run, artifact, parent, or source invocation"
            ) from exc
        return invocation

    def publish(
        self,
        publication: PipelinePublicationRecord,
        updated_run: PipelineRunRecord,
    ) -> PipelinePublicationRecord:
        try:
            with psycopg.connect(self._database_url) as connection:
                connection.execute(
                    """
                    INSERT INTO pipeline_publications (
                        publication_id, run_id, case_id, publication_json, trace_id, created_at
                    ) VALUES (%s, %s, %s, %s, %s, %s)
                    """,
                    (
                        publication.publication_id,
                        publication.run_id,
                        publication.case_id,
                        Jsonb(publication.model_dump(mode="json")),
                        publication.trace_id,
                        publication.created_at,
                    ),
                )
                _insert_artifact_rows(
                    connection,
                    "pipeline_publication_output_artifacts",
                    "publication_id",
                    publication.publication_id,
                    publication.output_artifacts,
                )
                row = connection.execute(
                    """
                    UPDATE pipeline_runs
                    SET run_status = %s, run_json = %s, updated_at = %s
                    WHERE run_id = %s
                    RETURNING run_id
                    """,
                    (
                        updated_run.status.value,
                        Jsonb(updated_run.model_dump(mode="json")),
                        updated_run.updated_at,
                        updated_run.run_id,
                    ),
                ).fetchone()
                if row is None:
                    raise EvaluationNotFound(f"pipeline run {updated_run.run_id} does not exist")
        except errors.UniqueViolation as exc:
            raise DuplicateEvaluationRecord(
                f"pipeline publication {publication.publication_id} already exists"
            ) from exc
        except errors.ForeignKeyViolation as exc:
            raise InvalidEvaluationState(
                "pipeline publication references an unknown run, case, or artifact"
            ) from exc
        return publication

    def list_publications(self, run_id: UUID) -> Sequence[PipelinePublicationRecord]:
        with psycopg.connect(self._database_url, row_factory=dict_row) as connection:
            rows = connection.execute(
                """
                SELECT publication_json FROM pipeline_publications
                WHERE run_id = %s ORDER BY created_at, publication_id
                """,
                (run_id,),
            ).fetchall()
        return tuple(_model(PipelinePublicationRecord, row["publication_json"]) for row in rows)


def _insert_invocation(
    connection: Any,
    invocation: ComponentInvocationRecord,
    *,
    include_parents: bool,
) -> None:
    connection.execute(
        """
        INSERT INTO component_invocations (
            invocation_id, run_id, component_key, invocation_status,
            replay_of_invocation_id, invocation_json, trace_id, created_at, completed_at
        ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s)
        """,
        (
            invocation.invocation_id,
            invocation.run_id,
            invocation.component.component_key,
            invocation.status.value,
            invocation.replay_of_invocation_id,
            Jsonb(invocation.model_dump(mode="json")),
            invocation.trace_id,
            invocation.created_at,
            invocation.result.completed_at if invocation.result is not None else None,
        ),
    )
    _insert_artifact_rows(
        connection,
        "component_invocation_input_artifacts",
        "invocation_id",
        invocation.invocation_id,
        invocation.input_artifacts,
    )
    if include_parents:
        _insert_parent_rows(connection, invocation)


def _insert_parent_rows(connection: Any, invocation: ComponentInvocationRecord) -> None:
    for position, parent_id in enumerate(invocation.parent_invocation_ids):
        connection.execute(
            """
            INSERT INTO component_invocation_parents (
                run_id, invocation_id, position, parent_invocation_id
            ) VALUES (%s, %s, %s, %s)
            """,
            (invocation.run_id, invocation.invocation_id, position, parent_id),
        )


def _insert_artifact_rows(
    connection: Any,
    table: str,
    owner_column: str,
    owner_id: UUID,
    artifacts: Sequence[Any],
) -> None:
    allowed = {
        ("pipeline_run_input_artifacts", "run_id"),
        ("component_invocation_input_artifacts", "invocation_id"),
        ("component_invocation_output_artifacts", "invocation_id"),
        ("pipeline_publication_output_artifacts", "publication_id"),
    }
    if (table, owner_column) not in allowed:
        raise ValueError("unsupported pipeline artifact association")
    for position, artifact in enumerate(artifacts):
        connection.execute(
            f"INSERT INTO {table} ({owner_column}, position, artifact_id) VALUES (%s, %s, %s)",
            (owner_id, position, artifact.artifact_id),
        )


def _model(model_type: Any, value: object) -> Any:
    return model_type.model_validate_json(json.dumps(value))
