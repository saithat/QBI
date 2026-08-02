"""Persistence boundary for draft and frozen golden datasets."""

from __future__ import annotations

from collections.abc import Sequence
from typing import Protocol
from uuid import UUID

from hiveblot_contracts import (
    GoldenCaseSourceArtifact,
    GoldenCaseTransition,
    GoldenDatasetDraftDetail,
    GoldenDatasetExportRecord,
    GoldenDatasetMember,
    GoldenDatasetRecord,
    GoldenDatasetSnapshot,
)


class GoldenDatasetRepository(Protocol):
    def create_dataset(self, record: GoldenDatasetRecord) -> GoldenDatasetRecord: ...

    def get_dataset(self, dataset_id: UUID) -> GoldenDatasetRecord | None: ...

    def list_datasets(self, *, limit: int) -> Sequence[GoldenDatasetRecord]: ...

    def get_detail(self, dataset_id: UUID) -> GoldenDatasetDraftDetail | None: ...

    def delete_draft(self, dataset_id: UUID, *, expected_revision: int) -> None: ...

    def add_member(
        self,
        member: GoldenDatasetMember,
        source_artifacts: tuple[GoldenCaseSourceArtifact, ...],
        transition: GoldenCaseTransition,
        *,
        expected_dataset_revision: int,
        updated_dataset: GoldenDatasetRecord,
    ) -> GoldenDatasetMember: ...

    def get_member(self, dataset_id: UUID, case_id: UUID) -> GoldenDatasetMember | None: ...

    def transition_member(
        self,
        member: GoldenDatasetMember,
        transition: GoldenCaseTransition,
        *,
        expected_dataset_revision: int,
        expected_state_version: int,
        updated_dataset: GoldenDatasetRecord,
    ) -> GoldenDatasetMember: ...

    def list_transitions(
        self,
        dataset_id: UUID,
        case_id: UUID,
    ) -> Sequence[GoldenCaseTransition]: ...

    def freeze(
        self,
        snapshot: GoldenDatasetSnapshot,
        updated_dataset: GoldenDatasetRecord,
        *,
        expected_dataset_revision: int,
    ) -> GoldenDatasetSnapshot: ...

    def get_snapshot(self, snapshot_id: UUID) -> GoldenDatasetSnapshot | None: ...

    def get_snapshot_by_dataset(self, dataset_id: UUID) -> GoldenDatasetSnapshot | None: ...

    def create_export(self, record: GoldenDatasetExportRecord) -> GoldenDatasetExportRecord: ...

    def get_export(self, snapshot_id: UUID) -> GoldenDatasetExportRecord | None: ...
