"""HTTP-only schemas for golden-dataset lifecycle, snapshots, and exports."""

from __future__ import annotations

from datetime import datetime
from typing import Literal
from uuid import UUID

from hiveblot_contracts import ContractModel
from pydantic import Field

from .evaluation_schemas import JsonTuple, JsonUUID

DatasetTypeInput = Literal[
    "development",
    "frozen_test",
    "challenge",
    "shadow",
    "densitometry_reference",
    "retrieval_evaluation",
]
DatasetStatusResponse = Literal["draft", "frozen"]
DatasetSplitInput = Literal["train", "validation", "test"]
GoldenCaseStateInput = Literal[
    "unlabeled",
    "predicted",
    "reviewed",
    "needs_adjudication",
    "adjudicated",
    "gold_candidate",
    "gold",
    "retired",
]


class CreateGoldenDatasetRequest(ContractModel):
    dataset_name: str = Field(min_length=1, max_length=200)
    dataset_version: str = Field(min_length=1, max_length=200)
    dataset_type: DatasetTypeInput
    predecessor_snapshot_id: JsonUUID | None = None
    created_by: JsonUUID


class AddGoldenCaseRequest(ContractModel):
    expected_dataset_revision: int = Field(ge=1)
    paper_key: str = Field(min_length=1, max_length=200)
    split: DatasetSplitInput
    actor_id: JsonUUID
    rationale: str = Field(min_length=1, max_length=10_000)


class PromoteGoldenCaseRequest(ContractModel):
    expected_dataset_revision: int = Field(ge=1)
    expected_state_version: int = Field(ge=1)
    target_state: GoldenCaseStateInput
    selected_revision_id: JsonUUID | None = None
    actor_id: JsonUUID
    rationale: str = Field(min_length=1, max_length=10_000)


class FreezeGoldenDatasetRequest(ContractModel):
    expected_dataset_revision: int = Field(ge=1)
    frozen_by: JsonUUID


class DeleteGoldenDatasetRequest(ContractModel):
    expected_dataset_revision: int = Field(ge=1)


class PublishGoldenExportRequest(ContractModel):
    pass


class GoldenDatasetResponse(ContractModel):
    dataset_id: UUID
    dataset_name: str
    dataset_version: str
    dataset_type: DatasetTypeInput
    status: DatasetStatusResponse
    predecessor_snapshot_id: UUID | None
    revision: int
    created_by: UUID
    created_at: datetime
    updated_at: datetime


class GoldenDatasetMemberResponse(ContractModel):
    case_id: UUID
    paper_key: str
    split: DatasetSplitInput
    state: GoldenCaseStateInput
    selected_revision_id: UUID | None
    state_version: int
    added_by: UUID
    added_at: datetime
    updated_at: datetime


class GoldenDatasetDetailResponse(ContractModel):
    dataset: GoldenDatasetResponse
    members: tuple[GoldenDatasetMemberResponse, ...]


class GoldenDatasetListResponse(ContractModel):
    datasets: tuple[GoldenDatasetResponse, ...]


class GoldenTransitionResponse(ContractModel):
    transition_id: UUID
    case_id: UUID
    from_state: GoldenCaseStateInput | None
    to_state: GoldenCaseStateInput
    selected_revision_id: UUID | None
    actor_id: UUID
    rationale: str
    created_at: datetime


class GoldenTransitionListResponse(ContractModel):
    dataset_id: UUID
    case_id: UUID
    transitions: tuple[GoldenTransitionResponse, ...]


class GoldenArtifactSummaryResponse(ContractModel):
    artifact_id: UUID
    role: Literal["source_document", "figure", "raw_source", "supplementary", "context"]
    page_number: int | None
    sha256: str
    media_type: str
    byte_size: int


class GoldenCaseSnapshotSummaryResponse(ContractModel):
    case_id: UUID
    case_key: str
    paper_key: str
    split: DatasetSplitInput
    annotation_revision_id: UUID
    annotation_schema_version: str
    review_status: Literal[
        "unreviewed", "in_review", "reviewed", "needs_adjudication", "adjudicated"
    ]
    adjudication_id: UUID | None
    artifacts: tuple[GoldenArtifactSummaryResponse, ...]


class GoldenChangeResponse(ContractModel):
    case_id: UUID
    change_type: Literal["added", "removed", "modified"]
    changed_fields: JsonTuple[
        Literal["split", "annotation_revision", "source_artifacts", "case_record"]
    ]
    previous_case_sha256: str | None
    current_case_sha256: str | None
    previous_split: DatasetSplitInput | None
    current_split: DatasetSplitInput | None
    previous_revision_id: UUID | None
    current_revision_id: UUID | None


class GoldenDatasetSnapshotResponse(ContractModel):
    snapshot_id: UUID
    dataset_id: UUID
    dataset_name: str
    dataset_version: str
    dataset_type: DatasetTypeInput
    predecessor_snapshot_id: UUID | None
    content_sha256: str
    frozen_by: UUID
    frozen_at: datetime
    cases: tuple[GoldenCaseSnapshotSummaryResponse, ...]
    changes: tuple[GoldenChangeResponse, ...]


class GoldenExportObjectResponse(ContractModel):
    filename: str
    media_type: str
    sha256: str
    byte_size: int
    storage_key: str


class GoldenExportResponse(ContractModel):
    export_id: UUID
    snapshot_id: UUID
    cases_jsonl: GoldenExportObjectResponse
    manifest_json: GoldenExportObjectResponse
    created_at: datetime


class GoldenExportDownloadResponse(ContractModel):
    export_id: UUID
    export_kind: Literal["cases_jsonl", "manifest_json"]
    url: str
    expires_at: datetime
