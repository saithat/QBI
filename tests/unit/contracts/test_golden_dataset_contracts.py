from datetime import UTC, datetime
from uuid import uuid4

import pytest
from hiveblot_contracts import (
    GoldenCaseState,
    GoldenDatasetMember,
    GoldenDatasetRecord,
    GoldenDatasetSplit,
    GoldenDatasetStatus,
    GoldenDatasetType,
)
from pydantic import ValidationError

NOW = datetime(2026, 8, 3, tzinfo=UTC)


def test_golden_contracts_are_strict_frozen_and_versioned() -> None:
    dataset = GoldenDatasetRecord(
        dataset_id=uuid4(),
        dataset_name="western-blot-gold",
        dataset_version="1.0",
        dataset_type=GoldenDatasetType.FROZEN_TEST,
        status=GoldenDatasetStatus.DRAFT,
        revision=1,
        created_by=uuid4(),
        created_at=NOW,
        updated_at=NOW,
    )

    assert dataset.schema_version == "1.0"
    with pytest.raises(ValidationError, match="frozen"):
        dataset.dataset_name = "changed"
    with pytest.raises(ValidationError):
        GoldenDatasetRecord.model_validate({**dataset.model_dump(mode="python"), "revision": "1"})
    with pytest.raises(ValidationError, match="Extra inputs"):
        GoldenDatasetRecord.model_validate({**dataset.model_dump(mode="python"), "unknown": True})


def test_reviewed_and_gold_members_require_an_exact_annotation_revision() -> None:
    payload = {
        "dataset_id": uuid4(),
        "case_id": uuid4(),
        "paper_key": "doi:10.1000/example",
        "split": GoldenDatasetSplit.TEST,
        "state": GoldenCaseState.REVIEWED,
        "selected_revision_id": None,
        "state_version": 3,
        "added_by": uuid4(),
        "added_at": NOW,
        "updated_at": NOW,
    }
    with pytest.raises(ValidationError, match="selected revision"):
        GoldenDatasetMember(**payload)

    payload["selected_revision_id"] = uuid4()
    member = GoldenDatasetMember(**payload)
    assert member.state is GoldenCaseState.REVIEWED
