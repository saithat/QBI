from __future__ import annotations

import hashlib
from uuid import UUID

from hiveblot_contracts import (
    GoldenCaseSourceArtifact,
    GoldenCaseTransition,
    GoldenDatasetDraftDetail,
    GoldenDatasetExportObject,
    GoldenDatasetExportRecord,
    GoldenDatasetMember,
    GoldenDatasetRecord,
    GoldenDatasetSnapshot,
    GoldenDatasetStatus,
)
from hiveblot_evaluation import (
    ConcurrencyConflict,
    DuplicateEvaluationRecord,
    EvaluationNotFound,
    InvalidEvaluationState,
)


class InMemoryGoldenDatasetRepository:
    def __init__(self) -> None:
        self.datasets: dict[UUID, GoldenDatasetRecord] = {}
        self.members: dict[tuple[UUID, UUID], GoldenDatasetMember] = {}
        self.member_sources: dict[tuple[UUID, UUID], tuple[GoldenCaseSourceArtifact, ...]] = {}
        self.transitions: dict[tuple[UUID, UUID], list[GoldenCaseTransition]] = {}
        self.snapshots: dict[UUID, GoldenDatasetSnapshot] = {}
        self.exports: dict[UUID, GoldenDatasetExportRecord] = {}

    def create_dataset(self, record: GoldenDatasetRecord) -> GoldenDatasetRecord:
        if any(
            (item.dataset_name, item.dataset_version)
            == (record.dataset_name, record.dataset_version)
            for item in self.datasets.values()
        ):
            raise DuplicateEvaluationRecord("dataset version already exists")
        if (
            record.predecessor_snapshot_id is not None
            and record.predecessor_snapshot_id not in self.snapshots
        ):
            raise EvaluationNotFound("predecessor snapshot does not exist")
        self.datasets[record.dataset_id] = record
        return record

    def get_dataset(self, dataset_id: UUID) -> GoldenDatasetRecord | None:
        return self.datasets.get(dataset_id)

    def list_datasets(self, *, limit: int):
        return tuple(
            sorted(
                self.datasets.values(),
                key=lambda item: (item.created_at, str(item.dataset_id)),
                reverse=True,
            )[:limit]
        )

    def get_detail(self, dataset_id: UUID) -> GoldenDatasetDraftDetail | None:
        dataset = self.datasets.get(dataset_id)
        if dataset is None:
            return None
        members = tuple(
            sorted(
                (
                    item
                    for (stored_dataset_id, _), item in self.members.items()
                    if stored_dataset_id == dataset_id
                ),
                key=lambda item: str(item.case_id),
            )
        )
        return GoldenDatasetDraftDetail(dataset=dataset, members=members)

    def delete_draft(self, dataset_id: UUID, *, expected_revision: int) -> None:
        dataset = self._draft(dataset_id, expected_revision)
        assert dataset.status is GoldenDatasetStatus.DRAFT
        del self.datasets[dataset_id]
        keys = [key for key in self.members if key[0] == dataset_id]
        for key in keys:
            del self.members[key]
            self.member_sources.pop(key, None)
            self.transitions.pop(key, None)

    def add_member(
        self,
        member: GoldenDatasetMember,
        source_artifacts: tuple[GoldenCaseSourceArtifact, ...],
        transition: GoldenCaseTransition,
        *,
        expected_dataset_revision: int,
        updated_dataset: GoldenDatasetRecord,
    ) -> GoldenDatasetMember:
        self._draft(member.dataset_id, expected_dataset_revision)
        key = (member.dataset_id, member.case_id)
        if key in self.members:
            raise DuplicateEvaluationRecord("member already exists")
        for stored_key, stored in self.members.items():
            if stored_key[0] != member.dataset_id:
                continue
            if stored.paper_key == member.paper_key and stored.split is not member.split:
                raise InvalidEvaluationState("paper already belongs to another split")
            stored_hashes = {
                item.artifact.sha256 for item in self.member_sources.get(stored_key, ())
            }
            if (
                stored_hashes & {item.artifact.sha256 for item in source_artifacts}
                and stored.split is not member.split
            ):
                raise InvalidEvaluationState("artifact content appears in another split")
        self.members[key] = member
        self.member_sources[key] = source_artifacts
        self.transitions[key] = [transition]
        self.datasets[member.dataset_id] = updated_dataset
        return member

    def get_member(self, dataset_id: UUID, case_id: UUID) -> GoldenDatasetMember | None:
        return self.members.get((dataset_id, case_id))

    def transition_member(
        self,
        member: GoldenDatasetMember,
        transition: GoldenCaseTransition,
        *,
        expected_dataset_revision: int,
        expected_state_version: int,
        updated_dataset: GoldenDatasetRecord,
    ) -> GoldenDatasetMember:
        self._draft(member.dataset_id, expected_dataset_revision)
        key = (member.dataset_id, member.case_id)
        current = self.members.get(key)
        if current is None:
            raise EvaluationNotFound("member does not exist")
        if current.state_version != expected_state_version:
            raise ConcurrencyConflict("member state changed")
        self.members[key] = member
        self.transitions.setdefault(key, []).append(transition)
        self.datasets[member.dataset_id] = updated_dataset
        return member

    def list_transitions(self, dataset_id: UUID, case_id: UUID):
        return tuple(self.transitions.get((dataset_id, case_id), ()))

    def freeze(
        self,
        snapshot: GoldenDatasetSnapshot,
        updated_dataset: GoldenDatasetRecord,
        *,
        expected_dataset_revision: int,
    ) -> GoldenDatasetSnapshot:
        self._draft(snapshot.dataset_id, expected_dataset_revision)
        if snapshot.snapshot_id in self.snapshots or any(
            item.dataset_id == snapshot.dataset_id for item in self.snapshots.values()
        ):
            raise DuplicateEvaluationRecord("dataset already frozen")
        self.snapshots[snapshot.snapshot_id] = snapshot
        self.datasets[snapshot.dataset_id] = updated_dataset
        return snapshot

    def get_snapshot(self, snapshot_id: UUID) -> GoldenDatasetSnapshot | None:
        return self.snapshots.get(snapshot_id)

    def get_snapshot_by_dataset(self, dataset_id: UUID) -> GoldenDatasetSnapshot | None:
        return next(
            (item for item in self.snapshots.values() if item.dataset_id == dataset_id),
            None,
        )

    def create_export(self, record: GoldenDatasetExportRecord) -> GoldenDatasetExportRecord:
        existing = self.exports.get(record.snapshot_id)
        if existing is not None and existing != record:
            raise DuplicateEvaluationRecord("snapshot has another export")
        self.exports[record.snapshot_id] = record
        return record

    def get_export(self, snapshot_id: UUID) -> GoldenDatasetExportRecord | None:
        return self.exports.get(snapshot_id)

    def _draft(self, dataset_id: UUID, expected_revision: int) -> GoldenDatasetRecord:
        dataset = self.datasets.get(dataset_id)
        if dataset is None:
            raise EvaluationNotFound("dataset does not exist")
        if dataset.status is not GoldenDatasetStatus.DRAFT:
            raise InvalidEvaluationState("dataset is frozen")
        if dataset.revision != expected_revision:
            raise ConcurrencyConflict("dataset revision changed")
        return dataset


class InMemoryDatasetExportStore:
    def __init__(self) -> None:
        self.objects: dict[str, bytes] = {}
        self.publication_count = 0

    def publish(
        self,
        *,
        filename: str,
        media_type: str,
        content: bytes,
    ) -> GoldenDatasetExportObject:
        sha256 = hashlib.sha256(content).hexdigest()
        key = f"dataset-exports/sha256/{sha256}/{filename}"
        if key not in self.objects:
            self.objects[key] = content
            self.publication_count += 1
        return GoldenDatasetExportObject(
            filename=filename,
            media_type=media_type,
            sha256=sha256,
            byte_size=len(content),
            storage_key=key,
        )

    def download_url(self, value: GoldenDatasetExportObject, *, expires_in: int) -> str:
        return f"https://objects.test/{value.storage_key}?expires={expires_in}"
