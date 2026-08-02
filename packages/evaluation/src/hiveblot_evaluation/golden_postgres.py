"""PostgreSQL mapping for golden-dataset drafts, snapshots, and exports."""

from __future__ import annotations

import json
from collections.abc import Sequence
from typing import Any
from uuid import UUID

import psycopg
from hiveblot_contracts import (
    GoldenCaseSourceArtifact,
    GoldenCaseTransition,
    GoldenDatasetDraftDetail,
    GoldenDatasetExportRecord,
    GoldenDatasetMember,
    GoldenDatasetRecord,
    GoldenDatasetSnapshot,
    GoldenDatasetStatus,
)
from psycopg import errors
from psycopg.rows import dict_row
from psycopg.types.json import Jsonb
from pydantic import BaseModel

from .errors import (
    ConcurrencyConflict,
    DuplicateEvaluationRecord,
    EvaluationNotFound,
    InvalidEvaluationState,
)


class PostgresGoldenDatasetRepository:
    def __init__(self, database_url: str) -> None:
        self._database_url = database_url

    def create_dataset(self, record: GoldenDatasetRecord) -> GoldenDatasetRecord:
        try:
            with psycopg.connect(self._database_url) as connection:
                connection.execute(
                    """
                    INSERT INTO golden_datasets (
                        dataset_id, dataset_name, dataset_version, dataset_type,
                        dataset_status, predecessor_snapshot_id, revision, created_by,
                        dataset_json, created_at, updated_at
                    ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
                    """,
                    (
                        record.dataset_id,
                        record.dataset_name,
                        record.dataset_version,
                        record.dataset_type.value,
                        record.status.value,
                        record.predecessor_snapshot_id,
                        record.revision,
                        record.created_by,
                        Jsonb(record.model_dump(mode="json")),
                        record.created_at,
                        record.updated_at,
                    ),
                )
        except errors.UniqueViolation as exc:
            raise DuplicateEvaluationRecord(
                f"golden dataset {record.dataset_name}@{record.dataset_version} already exists"
            ) from exc
        except errors.ForeignKeyViolation as exc:
            raise EvaluationNotFound(
                f"predecessor snapshot {record.predecessor_snapshot_id} does not exist"
            ) from exc
        return record

    def get_dataset(self, dataset_id: UUID) -> GoldenDatasetRecord | None:
        with psycopg.connect(self._database_url, row_factory=dict_row) as connection:
            row = connection.execute(
                "SELECT dataset_json FROM golden_datasets WHERE dataset_id = %s",
                (dataset_id,),
            ).fetchone()
        return _model(GoldenDatasetRecord, row["dataset_json"]) if row else None

    def list_datasets(self, *, limit: int) -> Sequence[GoldenDatasetRecord]:
        with psycopg.connect(self._database_url, row_factory=dict_row) as connection:
            rows = connection.execute(
                """
                SELECT dataset_json FROM golden_datasets
                ORDER BY created_at DESC, dataset_id DESC LIMIT %s
                """,
                (limit,),
            ).fetchall()
        return tuple(_model(GoldenDatasetRecord, row["dataset_json"]) for row in rows)

    def get_detail(self, dataset_id: UUID) -> GoldenDatasetDraftDetail | None:
        with psycopg.connect(self._database_url, row_factory=dict_row) as connection:
            dataset_row = connection.execute(
                "SELECT dataset_json FROM golden_datasets WHERE dataset_id = %s",
                (dataset_id,),
            ).fetchone()
            if dataset_row is None:
                return None
            member_rows = connection.execute(
                """
                SELECT member_json FROM golden_dataset_members
                WHERE dataset_id = %s ORDER BY case_id
                """,
                (dataset_id,),
            ).fetchall()
        return GoldenDatasetDraftDetail(
            dataset=_model(GoldenDatasetRecord, dataset_row["dataset_json"]),
            members=tuple(_model(GoldenDatasetMember, row["member_json"]) for row in member_rows),
        )

    def delete_draft(self, dataset_id: UUID, *, expected_revision: int) -> None:
        with psycopg.connect(self._database_url, row_factory=dict_row) as connection:
            row = _locked_dataset(connection, dataset_id)
            _check_draft_revision(row, dataset_id, expected_revision)
            connection.execute("DELETE FROM golden_datasets WHERE dataset_id = %s", (dataset_id,))

    def add_member(
        self,
        member: GoldenDatasetMember,
        source_artifacts: tuple[GoldenCaseSourceArtifact, ...],
        transition: GoldenCaseTransition,
        *,
        expected_dataset_revision: int,
        updated_dataset: GoldenDatasetRecord,
    ) -> GoldenDatasetMember:
        try:
            with psycopg.connect(self._database_url, row_factory=dict_row) as connection:
                row = _locked_dataset(connection, member.dataset_id)
                _check_draft_revision(row, member.dataset_id, expected_dataset_revision)
                paper = connection.execute(
                    """
                    SELECT split FROM golden_dataset_papers
                    WHERE dataset_id = %s AND paper_key = %s
                    """,
                    (member.dataset_id, member.paper_key),
                ).fetchone()
                if paper is not None and paper["split"] != member.split.value:
                    raise InvalidEvaluationState(
                        f"paper {member.paper_key!r} already belongs to split {paper['split']}"
                    )
                connection.execute(
                    """
                    INSERT INTO golden_dataset_papers (dataset_id, paper_key, split)
                    VALUES (%s, %s, %s) ON CONFLICT (dataset_id, paper_key) DO NOTHING
                    """,
                    (member.dataset_id, member.paper_key, member.split.value),
                )
                for source in source_artifacts:
                    content = connection.execute(
                        """
                        SELECT split FROM golden_dataset_content_hashes
                        WHERE dataset_id = %s AND sha256 = %s
                        """,
                        (member.dataset_id, source.artifact.sha256),
                    ).fetchone()
                    if content is not None and content["split"] != member.split.value:
                        raise InvalidEvaluationState(
                            f"artifact content {source.artifact.sha256} already belongs to "
                            f"split {content['split']}"
                        )
                    connection.execute(
                        """
                        INSERT INTO golden_dataset_content_hashes (dataset_id, sha256, split)
                        VALUES (%s, %s, %s) ON CONFLICT (dataset_id, sha256) DO NOTHING
                        """,
                        (member.dataset_id, source.artifact.sha256, member.split.value),
                    )
                connection.execute(
                    """
                    INSERT INTO golden_dataset_members (
                        dataset_id, case_id, paper_key, split, case_state,
                        selected_revision_id, state_version, added_by, member_json,
                        added_at, updated_at
                    ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
                    """,
                    _member_parameters(member),
                )
                for source in source_artifacts:
                    connection.execute(
                        """
                        INSERT INTO golden_dataset_member_artifacts (
                            dataset_id, case_id, artifact_id, artifact_role,
                            page_number, sha256, split
                        ) VALUES (%s, %s, %s, %s, %s, %s, %s)
                        """,
                        (
                            member.dataset_id,
                            member.case_id,
                            source.artifact.artifact_id,
                            source.role.value,
                            source.page_number,
                            source.artifact.sha256,
                            member.split.value,
                        ),
                    )
                _insert_transition(connection, transition)
                _update_dataset(connection, updated_dataset, expected_dataset_revision)
        except errors.UniqueViolation as exc:
            raise DuplicateEvaluationRecord(
                f"case {member.case_id} already belongs to golden dataset {member.dataset_id}"
            ) from exc
        except errors.ForeignKeyViolation as exc:
            raise InvalidEvaluationState(
                "golden member references an unknown case, artifact, paper split, or content split"
            ) from exc
        return member

    def get_member(self, dataset_id: UUID, case_id: UUID) -> GoldenDatasetMember | None:
        with psycopg.connect(self._database_url, row_factory=dict_row) as connection:
            row = connection.execute(
                """
                SELECT member_json FROM golden_dataset_members
                WHERE dataset_id = %s AND case_id = %s
                """,
                (dataset_id, case_id),
            ).fetchone()
        return _model(GoldenDatasetMember, row["member_json"]) if row else None

    def transition_member(
        self,
        member: GoldenDatasetMember,
        transition: GoldenCaseTransition,
        *,
        expected_dataset_revision: int,
        expected_state_version: int,
        updated_dataset: GoldenDatasetRecord,
    ) -> GoldenDatasetMember:
        try:
            with psycopg.connect(self._database_url, row_factory=dict_row) as connection:
                row = _locked_dataset(connection, member.dataset_id)
                _check_draft_revision(row, member.dataset_id, expected_dataset_revision)
                changed = connection.execute(
                    """
                    UPDATE golden_dataset_members
                    SET case_state = %s, selected_revision_id = %s,
                        state_version = %s, member_json = %s, updated_at = %s
                    WHERE dataset_id = %s AND case_id = %s AND state_version = %s
                    RETURNING case_id
                    """,
                    (
                        member.state.value,
                        member.selected_revision_id,
                        member.state_version,
                        Jsonb(member.model_dump(mode="json")),
                        member.updated_at,
                        member.dataset_id,
                        member.case_id,
                        expected_state_version,
                    ),
                ).fetchone()
                if changed is None:
                    exists = connection.execute(
                        """
                        SELECT state_version FROM golden_dataset_members
                        WHERE dataset_id = %s AND case_id = %s
                        """,
                        (member.dataset_id, member.case_id),
                    ).fetchone()
                    if exists is None:
                        raise EvaluationNotFound(
                            f"case {member.case_id} is not a golden dataset member"
                        )
                    raise ConcurrencyConflict(
                        f"golden member no longer has state version {expected_state_version}"
                    )
                _insert_transition(connection, transition)
                _update_dataset(connection, updated_dataset, expected_dataset_revision)
        except errors.ForeignKeyViolation as exc:
            raise InvalidEvaluationState(
                "golden transition references an unknown annotation revision"
            ) from exc
        return member

    def list_transitions(
        self,
        dataset_id: UUID,
        case_id: UUID,
    ) -> Sequence[GoldenCaseTransition]:
        with psycopg.connect(self._database_url, row_factory=dict_row) as connection:
            rows = connection.execute(
                """
                SELECT transition_json FROM golden_case_transitions
                WHERE dataset_id = %s AND case_id = %s
                ORDER BY created_at, transition_id
                """,
                (dataset_id, case_id),
            ).fetchall()
        return tuple(_model(GoldenCaseTransition, row["transition_json"]) for row in rows)

    def freeze(
        self,
        snapshot: GoldenDatasetSnapshot,
        updated_dataset: GoldenDatasetRecord,
        *,
        expected_dataset_revision: int,
    ) -> GoldenDatasetSnapshot:
        try:
            with psycopg.connect(self._database_url, row_factory=dict_row) as connection:
                row = _locked_dataset(connection, snapshot.dataset_id)
                _check_draft_revision(row, snapshot.dataset_id, expected_dataset_revision)
                connection.execute(
                    """
                    INSERT INTO golden_dataset_snapshots (
                        snapshot_id, dataset_id, dataset_name, dataset_version,
                        dataset_type, predecessor_snapshot_id, content_sha256,
                        snapshot_json, frozen_by, frozen_at
                    ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
                    """,
                    (
                        snapshot.snapshot_id,
                        snapshot.dataset_id,
                        snapshot.dataset_name,
                        snapshot.dataset_version,
                        snapshot.dataset_type.value,
                        snapshot.predecessor_snapshot_id,
                        snapshot.content_sha256,
                        Jsonb(snapshot.model_dump(mode="json")),
                        snapshot.frozen_by,
                        snapshot.frozen_at,
                    ),
                )
                _update_dataset(connection, updated_dataset, expected_dataset_revision)
        except errors.UniqueViolation as exc:
            raise DuplicateEvaluationRecord(
                f"golden dataset {snapshot.dataset_name}@{snapshot.dataset_version} is frozen"
            ) from exc
        except errors.ForeignKeyViolation as exc:
            raise EvaluationNotFound(
                f"predecessor snapshot {snapshot.predecessor_snapshot_id} does not exist"
            ) from exc
        return snapshot

    def get_snapshot(self, snapshot_id: UUID) -> GoldenDatasetSnapshot | None:
        with psycopg.connect(self._database_url, row_factory=dict_row) as connection:
            row = connection.execute(
                """
                SELECT snapshot_json FROM golden_dataset_snapshots WHERE snapshot_id = %s
                """,
                (snapshot_id,),
            ).fetchone()
        return _model(GoldenDatasetSnapshot, row["snapshot_json"]) if row else None

    def get_snapshot_by_dataset(self, dataset_id: UUID) -> GoldenDatasetSnapshot | None:
        with psycopg.connect(self._database_url, row_factory=dict_row) as connection:
            row = connection.execute(
                """
                SELECT snapshot_json FROM golden_dataset_snapshots WHERE dataset_id = %s
                """,
                (dataset_id,),
            ).fetchone()
        return _model(GoldenDatasetSnapshot, row["snapshot_json"]) if row else None

    def create_export(self, record: GoldenDatasetExportRecord) -> GoldenDatasetExportRecord:
        try:
            with psycopg.connect(self._database_url) as connection:
                connection.execute(
                    """
                    INSERT INTO golden_dataset_exports (
                        export_id, snapshot_id, cases_jsonl_sha256,
                        manifest_json_sha256, export_json, created_at
                    ) VALUES (%s, %s, %s, %s, %s, %s)
                    """,
                    (
                        record.export_id,
                        record.snapshot_id,
                        record.cases_jsonl.sha256,
                        record.manifest_json.sha256,
                        Jsonb(record.model_dump(mode="json")),
                        record.created_at,
                    ),
                )
        except errors.UniqueViolation as exc:
            existing = self.get_export(record.snapshot_id)
            if existing == record:
                return existing
            raise DuplicateEvaluationRecord(
                f"golden snapshot {record.snapshot_id} already has another export"
            ) from exc
        except errors.ForeignKeyViolation as exc:
            raise EvaluationNotFound(
                f"golden dataset snapshot {record.snapshot_id} does not exist"
            ) from exc
        return record

    def get_export(self, snapshot_id: UUID) -> GoldenDatasetExportRecord | None:
        with psycopg.connect(self._database_url, row_factory=dict_row) as connection:
            row = connection.execute(
                """
                SELECT export_json FROM golden_dataset_exports WHERE snapshot_id = %s
                """,
                (snapshot_id,),
            ).fetchone()
        return _model(GoldenDatasetExportRecord, row["export_json"]) if row else None


def _locked_dataset(connection: Any, dataset_id: UUID) -> dict[str, Any]:
    row = connection.execute(
        """
        SELECT dataset_status, revision FROM golden_datasets
        WHERE dataset_id = %s FOR UPDATE
        """,
        (dataset_id,),
    ).fetchone()
    if row is None:
        raise EvaluationNotFound(f"golden dataset {dataset_id} does not exist")
    return row


def _check_draft_revision(
    row: dict[str, Any],
    dataset_id: UUID,
    expected_revision: int,
) -> None:
    if row["dataset_status"] != GoldenDatasetStatus.DRAFT.value:
        raise InvalidEvaluationState("frozen golden datasets are immutable")
    if row["revision"] != expected_revision:
        raise ConcurrencyConflict(
            f"golden dataset {dataset_id} no longer has revision {expected_revision}"
        )


def _member_parameters(member: GoldenDatasetMember) -> tuple[object, ...]:
    return (
        member.dataset_id,
        member.case_id,
        member.paper_key,
        member.split.value,
        member.state.value,
        member.selected_revision_id,
        member.state_version,
        member.added_by,
        Jsonb(member.model_dump(mode="json")),
        member.added_at,
        member.updated_at,
    )


def _insert_transition(connection: Any, value: GoldenCaseTransition) -> None:
    connection.execute(
        """
        INSERT INTO golden_case_transitions (
            transition_id, dataset_id, case_id, from_state, to_state,
            selected_revision_id, actor_id, transition_json, created_at
        ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s)
        """,
        (
            value.transition_id,
            value.dataset_id,
            value.case_id,
            value.from_state.value if value.from_state is not None else None,
            value.to_state.value,
            value.selected_revision_id,
            value.actor_id,
            Jsonb(value.model_dump(mode="json")),
            value.created_at,
        ),
    )


def _update_dataset(
    connection: Any,
    value: GoldenDatasetRecord,
    expected_revision: int,
) -> None:
    changed = connection.execute(
        """
        UPDATE golden_datasets
        SET dataset_status = %s, revision = %s, dataset_json = %s, updated_at = %s
        WHERE dataset_id = %s AND revision = %s AND dataset_status = 'draft'
        """,
        (
            value.status.value,
            value.revision,
            Jsonb(value.model_dump(mode="json")),
            value.updated_at,
            value.dataset_id,
            expected_revision,
        ),
    ).rowcount
    if changed != 1:
        raise ConcurrencyConflict(
            f"golden dataset {value.dataset_id} changed during the transaction"
        )


def _model[T: BaseModel](model: type[T], value: object) -> T:
    return model.model_validate_json(json.dumps(value))
