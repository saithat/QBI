"""Golden-dataset lifecycle, frozen snapshot, and export contracts."""

from __future__ import annotations

import hashlib
import json
from collections.abc import Hashable, Iterable
from enum import StrEnum
from typing import Self
from uuid import NAMESPACE_URL, UUID, uuid5

from pydantic import AwareDatetime, Field, model_validator

from .annotations import (
    AdjudicationRecord,
    AnnotationRevision,
    CaseArtifactRole,
    ReviewStatus,
)
from .artifacts import ArtifactRecord
from .base import ContractModel, Identifier, MediaType, Sha256Digest


class GoldenDatasetType(StrEnum):
    DEVELOPMENT = "development"
    FROZEN_TEST = "frozen_test"
    CHALLENGE = "challenge"
    SHADOW = "shadow"
    DENSITOMETRY_REFERENCE = "densitometry_reference"
    RETRIEVAL_EVALUATION = "retrieval_evaluation"


class GoldenDatasetStatus(StrEnum):
    DRAFT = "draft"
    FROZEN = "frozen"


class GoldenDatasetSplit(StrEnum):
    TRAIN = "train"
    VALIDATION = "validation"
    TEST = "test"


class GoldenCaseState(StrEnum):
    UNLABELED = "unlabeled"
    PREDICTED = "predicted"
    REVIEWED = "reviewed"
    NEEDS_ADJUDICATION = "needs_adjudication"
    ADJUDICATED = "adjudicated"
    GOLD_CANDIDATE = "gold_candidate"
    GOLD = "gold"
    RETIRED = "retired"


class GoldenDatasetRecord(ContractModel):
    dataset_id: UUID
    dataset_name: Identifier
    dataset_version: Identifier
    dataset_type: GoldenDatasetType
    status: GoldenDatasetStatus
    predecessor_snapshot_id: UUID | None = None
    revision: int = Field(ge=1)
    created_by: UUID
    created_at: AwareDatetime
    updated_at: AwareDatetime

    @model_validator(mode="after")
    def timestamps_are_ordered(self) -> Self:
        if self.updated_at < self.created_at:
            raise ValueError("golden dataset updated_at cannot precede created_at")
        return self


class GoldenDatasetMember(ContractModel):
    dataset_id: UUID
    case_id: UUID
    paper_key: Identifier
    split: GoldenDatasetSplit
    state: GoldenCaseState
    selected_revision_id: UUID | None = None
    state_version: int = Field(ge=1)
    added_by: UUID
    added_at: AwareDatetime
    updated_at: AwareDatetime

    @model_validator(mode="after")
    def selected_revision_matches_state(self) -> Self:
        states_requiring_label = {
            GoldenCaseState.REVIEWED,
            GoldenCaseState.NEEDS_ADJUDICATION,
            GoldenCaseState.ADJUDICATED,
            GoldenCaseState.GOLD_CANDIDATE,
            GoldenCaseState.GOLD,
        }
        if self.state in states_requiring_label and self.selected_revision_id is None:
            raise ValueError(f"{self.state.value} members require a selected revision")
        if self.updated_at < self.added_at:
            raise ValueError("member updated_at cannot precede added_at")
        return self


class GoldenCaseTransition(ContractModel):
    transition_id: UUID
    dataset_id: UUID
    case_id: UUID
    from_state: GoldenCaseState | None
    to_state: GoldenCaseState
    selected_revision_id: UUID | None = None
    actor_id: UUID
    rationale: str = Field(min_length=1, max_length=10_000)
    created_at: AwareDatetime


class GoldenDatasetDraftDetail(ContractModel):
    dataset: GoldenDatasetRecord
    members: tuple[GoldenDatasetMember, ...]

    @model_validator(mode="after")
    def membership_is_consistent(self) -> Self:
        if any(member.dataset_id != self.dataset.dataset_id for member in self.members):
            raise ValueError("all golden members must belong to the dataset")
        _require_unique((member.case_id for member in self.members), "golden member case IDs")
        _validate_paper_splits((member.paper_key, member.split) for member in self.members)
        return self


class GoldenCaseSourceArtifact(ContractModel):
    role: CaseArtifactRole
    page_number: int | None = Field(default=None, ge=1)
    artifact: ArtifactRecord


class GoldenCaseProvenance(ContractModel):
    evaluation_case_version: int = Field(ge=1)
    review_status: ReviewStatus
    prediction_ids: tuple[UUID, ...]
    annotation_id: UUID
    selected_revision_id: UUID
    adjudication_id: UUID | None = None
    promoted_by: UUID
    promoted_at: AwareDatetime

    @model_validator(mode="after")
    def prediction_ids_are_unique(self) -> Self:
        _require_unique(self.prediction_ids, "golden provenance prediction IDs")
        return self


class GoldenDatasetCaseSnapshot(ContractModel):
    case_id: UUID
    case_key: Identifier
    paper_key: Identifier
    split: GoldenDatasetSplit
    assay_type: str = Field(pattern=r"^western_blot$")
    source_artifacts: tuple[GoldenCaseSourceArtifact, ...] = Field(min_length=1)
    annotation_revision: AnnotationRevision
    adjudication: AdjudicationRecord | None = None
    provenance: GoldenCaseProvenance

    @model_validator(mode="after")
    def case_snapshot_is_consistent(self) -> Self:
        if self.annotation_revision.revision_id != self.provenance.selected_revision_id:
            raise ValueError("snapshot annotation revision must match provenance")
        if self.annotation_revision.annotation_id != self.provenance.annotation_id:
            raise ValueError("snapshot annotation document must match provenance")
        if self.adjudication is not None:
            if self.adjudication.case_id != self.case_id:
                raise ValueError("snapshot adjudication must belong to the case")
            if self.adjudication.adjudication_id != self.provenance.adjudication_id:
                raise ValueError("snapshot adjudication must match provenance")
            if self.adjudication.selected_revision_id != self.annotation_revision.revision_id:
                raise ValueError("snapshot adjudication must select the exported revision")
        elif self.provenance.adjudication_id is not None:
            raise ValueError("adjudication provenance requires an adjudication record")
        identities = [
            (item.artifact.artifact_id, item.role, item.page_number)
            for item in self.source_artifacts
        ]
        _require_unique(identities, "golden source artifact associations")
        artifact_ids = {item.artifact.artifact_id for item in self.source_artifacts}
        spatial_source_ids = {
            item.region.source_artifact_id for item in self.annotation_revision.spatial_annotations
        }
        if not spatial_source_ids.issubset(artifact_ids):
            raise ValueError("golden spatial annotations must reference exported source artifacts")
        return self


class GoldenDatasetChangeType(StrEnum):
    ADDED = "added"
    REMOVED = "removed"
    MODIFIED = "modified"


class GoldenDatasetChangedField(StrEnum):
    SPLIT = "split"
    ANNOTATION_REVISION = "annotation_revision"
    SOURCE_ARTIFACTS = "source_artifacts"
    CASE_RECORD = "case_record"


class GoldenDatasetCaseChange(ContractModel):
    case_id: UUID
    change_type: GoldenDatasetChangeType
    changed_fields: tuple[GoldenDatasetChangedField, ...]
    previous_case_sha256: Sha256Digest | None = None
    current_case_sha256: Sha256Digest | None = None
    previous_split: GoldenDatasetSplit | None = None
    current_split: GoldenDatasetSplit | None = None
    previous_revision_id: UUID | None = None
    current_revision_id: UUID | None = None

    @model_validator(mode="after")
    def change_shape_matches_type(self) -> Self:
        _require_unique(self.changed_fields, "golden dataset changed fields")
        if self.change_type is GoldenDatasetChangeType.ADDED:
            if self.previous_case_sha256 is not None or self.current_case_sha256 is None:
                raise ValueError("added cases require only current case content")
        elif self.change_type is GoldenDatasetChangeType.REMOVED:
            if self.previous_case_sha256 is None or self.current_case_sha256 is not None:
                raise ValueError("removed cases require only previous case content")
        elif (
            self.previous_case_sha256 is None
            or self.current_case_sha256 is None
            or not self.changed_fields
        ):
            raise ValueError("modified cases require both content hashes and changed fields")
        return self


class GoldenDatasetChangelog(ContractModel):
    predecessor_snapshot_id: UUID | None = None
    changes: tuple[GoldenDatasetCaseChange, ...]

    @model_validator(mode="after")
    def cases_are_unique(self) -> Self:
        _require_unique((item.case_id for item in self.changes), "golden changelog case IDs")
        return self


class GoldenDatasetSnapshot(ContractModel):
    snapshot_id: UUID
    dataset_id: UUID
    dataset_name: Identifier
    dataset_version: Identifier
    dataset_type: GoldenDatasetType
    predecessor_snapshot_id: UUID | None = None
    content_sha256: Sha256Digest
    frozen_by: UUID
    frozen_at: AwareDatetime
    cases: tuple[GoldenDatasetCaseSnapshot, ...] = Field(min_length=1)
    changelog: GoldenDatasetChangelog

    @model_validator(mode="after")
    def snapshot_is_immutable_and_leakage_safe(self) -> Self:
        _require_unique((item.case_id for item in self.cases), "golden snapshot case IDs")
        _validate_paper_splits((item.paper_key, item.split) for item in self.cases)
        _validate_content_splits(self.cases)
        if self.changelog.predecessor_snapshot_id != self.predecessor_snapshot_id:
            raise ValueError("golden changelog predecessor must match the snapshot")
        cases_by_id = {item.case_id: item for item in self.cases}
        if self.predecessor_snapshot_id is None:
            added_ids = {
                item.case_id
                for item in self.changelog.changes
                if item.change_type is GoldenDatasetChangeType.ADDED
            }
            if added_ids != set(cases_by_id) or any(
                item.change_type is not GoldenDatasetChangeType.ADDED
                for item in self.changelog.changes
            ):
                raise ValueError("an initial golden snapshot changelog must add every case")
        for change in self.changelog.changes:
            current = cases_by_id.get(change.case_id)
            if change.change_type is GoldenDatasetChangeType.REMOVED:
                if current is not None:
                    raise ValueError("removed changelog cases cannot remain in the snapshot")
                continue
            if current is None:
                raise ValueError("added or modified changelog cases must exist in the snapshot")
            if change.current_case_sha256 != golden_dataset_case_sha256(current):
                raise ValueError("changelog current case hash does not match the snapshot")
            if change.current_split != current.split:
                raise ValueError("changelog current split does not match the snapshot")
            if change.current_revision_id != current.annotation_revision.revision_id:
                raise ValueError("changelog current revision does not match the snapshot")
        expected = golden_dataset_content_sha256(
            dataset_name=self.dataset_name,
            dataset_version=self.dataset_version,
            dataset_type=self.dataset_type,
            predecessor_snapshot_id=self.predecessor_snapshot_id,
            cases=self.cases,
            changelog=self.changelog,
        )
        if self.content_sha256 != expected:
            raise ValueError("golden dataset content_sha256 does not match its content")
        if self.snapshot_id != golden_dataset_snapshot_id(expected):
            raise ValueError("golden snapshot ID must be derived from its content hash")
        return self


class GoldenDatasetCaseExport(ContractModel):
    snapshot_id: UUID
    dataset_name: Identifier
    dataset_version: Identifier
    dataset_type: GoldenDatasetType
    case: GoldenDatasetCaseSnapshot


class GoldenDatasetExportObject(ContractModel):
    filename: str = Field(min_length=1, max_length=1024)
    media_type: MediaType
    sha256: Sha256Digest
    byte_size: int = Field(ge=0)
    storage_key: str = Field(min_length=1, max_length=2048)


class GoldenDatasetArtifactManifestEntry(ContractModel):
    artifact_id: UUID
    sha256: Sha256Digest
    media_type: MediaType
    byte_size: int = Field(ge=0)


class GoldenDatasetSplitSummary(ContractModel):
    split: GoldenDatasetSplit
    case_count: int = Field(ge=1)
    paper_count: int = Field(ge=1)


class GoldenDatasetExportManifest(ContractModel):
    snapshot_id: UUID
    dataset_name: Identifier
    dataset_version: Identifier
    dataset_type: GoldenDatasetType
    dataset_sha256: Sha256Digest
    frozen_at: AwareDatetime
    case_count: int = Field(ge=1)
    splits: tuple[GoldenDatasetSplitSummary, ...]
    artifacts: tuple[GoldenDatasetArtifactManifestEntry, ...]
    cases_jsonl: GoldenDatasetExportObject
    changelog: GoldenDatasetChangelog

    @model_validator(mode="after")
    def summaries_are_unique_and_complete(self) -> Self:
        _require_unique((item.split for item in self.splits), "golden export splits")
        _require_unique(
            (item.artifact_id for item in self.artifacts),
            "golden export artifact IDs",
        )
        if sum(item.case_count for item in self.splits) != self.case_count:
            raise ValueError("golden export split counts must equal the case count")
        return self


class GoldenDatasetExportRecord(ContractModel):
    export_id: UUID
    snapshot_id: UUID
    cases_jsonl: GoldenDatasetExportObject
    manifest_json: GoldenDatasetExportObject
    created_at: AwareDatetime


def golden_dataset_case_sha256(case: GoldenDatasetCaseSnapshot) -> str:
    return _sha256(case.model_dump(mode="json"))


def golden_dataset_content_sha256(
    *,
    dataset_name: str,
    dataset_version: str,
    dataset_type: GoldenDatasetType,
    predecessor_snapshot_id: UUID | None,
    cases: tuple[GoldenDatasetCaseSnapshot, ...],
    changelog: GoldenDatasetChangelog,
) -> str:
    payload = {
        "dataset_name": dataset_name,
        "dataset_version": dataset_version,
        "dataset_type": dataset_type.value,
        "predecessor_snapshot_id": (
            str(predecessor_snapshot_id) if predecessor_snapshot_id is not None else None
        ),
        "cases": [
            item.model_dump(mode="json")
            for item in sorted(cases, key=lambda value: str(value.case_id))
        ],
        "changelog": changelog.model_dump(mode="json"),
    }
    return _sha256(payload)


def golden_dataset_snapshot_id(content_sha256: str) -> UUID:
    return uuid5(NAMESPACE_URL, f"urn:hiveblot:golden-dataset-snapshot:{content_sha256}")


def _validate_paper_splits(values: Iterable[tuple[str, GoldenDatasetSplit]]) -> None:
    paper_splits: dict[str, GoldenDatasetSplit] = {}
    for paper_key, split in values:
        current = paper_splits.setdefault(paper_key, split)
        if current != split:
            raise ValueError(f"paper {paper_key!r} appears in multiple dataset splits")


def _validate_content_splits(cases: tuple[GoldenDatasetCaseSnapshot, ...]) -> None:
    content_splits: dict[str, GoldenDatasetSplit] = {}
    for case in cases:
        for source in case.source_artifacts:
            current = content_splits.setdefault(source.artifact.sha256, case.split)
            if current != case.split:
                raise ValueError(
                    f"artifact content {source.artifact.sha256} appears in multiple dataset splits"
                )


def _require_unique(values: Iterable[Hashable], label: str) -> None:
    materialized = tuple(values)
    if len(materialized) != len(set(materialized)):
        raise ValueError(f"{label} must be unique")


def _sha256(value: object) -> str:
    encoded = json.dumps(value, sort_keys=True, separators=(",", ":")).encode()
    return hashlib.sha256(encoded).hexdigest()
