import json
from uuid import UUID, uuid4

import pytest
from hiveblot_contracts import (
    GoldenCaseState,
    GoldenDatasetCaseExport,
    GoldenDatasetExportManifest,
    GoldenDatasetSplit,
    GoldenDatasetStatus,
    GoldenDatasetType,
)
from hiveblot_evaluation import (
    ConcurrencyConflict,
    GoldenDatasetExportPublisher,
    GoldenDatasetService,
    InvalidEvaluationState,
)

from tests.fakes.golden import InMemoryDatasetExportStore, InMemoryGoldenDatasetRepository
from tests.fakes.golden_fixture import (
    ReviewedCaseFixture,
    create_reviewed_case,
    evaluation_fixture,
    public_artifact,
)
from tests.fakes.structured import structured_annotation
from tests.fakes.workbench import InMemoryArtifactLookup


def test_gold_promotion_freeze_and_export_are_immutable_and_reproducible() -> None:
    evaluation, _, clock = evaluation_fixture()
    fixture = create_reviewed_case(
        evaluation,
        public_artifact("a"),
        case_key="paper-a:figure-1",
    )
    repository = InMemoryGoldenDatasetRepository()
    export_store = InMemoryDatasetExportStore()
    service = GoldenDatasetService(
        repository,
        evaluation,
        InMemoryArtifactLookup((fixture.artifact,)),
        GoldenDatasetExportPublisher(export_store),
        clock=clock,
    )
    actor_id = uuid4()
    dataset = service.create_dataset(
        dataset_name="western-blot-gold",
        dataset_version="1.0",
        dataset_type=GoldenDatasetType.FROZEN_TEST,
        predecessor_snapshot_id=None,
        created_by=actor_id,
    )
    detail = service.add_case(
        dataset.dataset_id,
        fixture.case_id,
        expected_dataset_revision=dataset.revision,
        paper_key="doi:10.1000/paper-a",
        split=GoldenDatasetSplit.TEST,
        actor_id=actor_id,
        rationale="Seed the frozen evaluation set.",
    )

    with pytest.raises(InvalidEvaluationState, match="cannot move"):
        service.promote_case(
            dataset.dataset_id,
            fixture.case_id,
            expected_dataset_revision=detail.dataset.revision,
            expected_state_version=detail.members[0].state_version,
            target_state=GoldenCaseState.GOLD,
            selected_revision_id=fixture.selected_revision_id,
            actor_id=actor_id,
            rationale="Invalid shortcut.",
        )

    detail = _promote_reviewed_case_to_gold(service, detail, fixture, actor_id)
    before_freeze_case = evaluation.get_case(fixture.case_id)
    snapshot = service.freeze(
        dataset.dataset_id,
        expected_dataset_revision=detail.dataset.revision,
        frozen_by=actor_id,
    )

    assert snapshot.cases[0].annotation_revision.revision_id == fixture.selected_revision_id
    assert snapshot.cases[0].source_artifacts[0].artifact.sha256 == "a" * 64
    assert snapshot.cases[0].provenance.prediction_ids
    assert snapshot.changelog.changes[0].change_type.value == "added"
    assert service.get_dataset(dataset.dataset_id).status is GoldenDatasetStatus.FROZEN
    assert evaluation.get_case(fixture.case_id) == before_freeze_case
    assert (
        service.freeze(
            dataset.dataset_id,
            expected_dataset_revision=1,
            frozen_by=uuid4(),
        )
        == snapshot
    )

    export = service.publish_export(snapshot.snapshot_id)
    repeated = service.publish_export(snapshot.snapshot_id)
    assert repeated == export
    assert export_store.publication_count == 2
    cases_bytes = export_store.objects[export.cases_jsonl.storage_key]
    manifest_bytes = export_store.objects[export.manifest_json.storage_key]
    lines = cases_bytes.decode().splitlines()
    assert len(lines) == 1
    case_export = GoldenDatasetCaseExport.model_validate_json(lines[0])
    manifest = GoldenDatasetExportManifest.model_validate_json(manifest_bytes)
    assert case_export.case.case_id == fixture.case_id
    assert manifest.dataset_sha256 == snapshot.content_sha256
    assert manifest.artifacts[0].sha256 == "a" * 64
    assert json.loads(manifest_bytes)["case_count"] == 1
    url, expires_at = service.create_export_download_url(
        snapshot.snapshot_id,
        export_kind="cases_jsonl",
    )
    assert export.cases_jsonl.storage_key in url
    assert expires_at > export.created_at

    with pytest.raises(InvalidEvaluationState, match="immutable"):
        service.add_case(
            dataset.dataset_id,
            fixture.case_id,
            expected_dataset_revision=detail.dataset.revision,
            paper_key="doi:10.1000/paper-a",
            split=GoldenDatasetSplit.TEST,
            actor_id=actor_id,
            rationale="Cannot mutate frozen data.",
        )
    with pytest.raises(InvalidEvaluationState, match="immutable"):
        service.delete_draft(
            dataset.dataset_id,
            expected_revision=service.get_dataset(dataset.dataset_id).revision,
        )


def test_paper_and_content_hash_leakage_are_rejected_across_splits() -> None:
    evaluation, _, clock = evaluation_fixture()
    first = create_reviewed_case(
        evaluation,
        public_artifact("b"),
        case_key="paper-b:figure-1",
    )
    second = create_reviewed_case(
        evaluation,
        public_artifact("c"),
        case_key="paper-b:figure-2",
    )
    duplicate_content = create_reviewed_case(
        evaluation,
        public_artifact("b"),
        case_key="paper-c:figure-1",
    )
    repository = InMemoryGoldenDatasetRepository()
    service = GoldenDatasetService(
        repository,
        evaluation,
        InMemoryArtifactLookup((first.artifact, second.artifact, duplicate_content.artifact)),
        clock=clock,
    )
    actor_id = uuid4()
    dataset = service.create_dataset(
        dataset_name="leakage-check",
        dataset_version="1.0",
        dataset_type=GoldenDatasetType.DEVELOPMENT,
        predecessor_snapshot_id=None,
        created_by=actor_id,
    )
    detail = service.add_case(
        dataset.dataset_id,
        first.case_id,
        expected_dataset_revision=1,
        paper_key="pmc:paper-b",
        split=GoldenDatasetSplit.TRAIN,
        actor_id=actor_id,
        rationale="Training case.",
    )
    with pytest.raises(InvalidEvaluationState, match="paper"):
        service.add_case(
            dataset.dataset_id,
            second.case_id,
            expected_dataset_revision=detail.dataset.revision,
            paper_key="pmc:paper-b",
            split=GoldenDatasetSplit.TEST,
            actor_id=actor_id,
            rationale="Would leak one paper.",
        )
    with pytest.raises(InvalidEvaluationState, match="artifact content"):
        service.add_case(
            dataset.dataset_id,
            duplicate_content.case_id,
            expected_dataset_revision=detail.dataset.revision,
            paper_key="pmc:paper-c",
            split=GoldenDatasetSplit.VALIDATION,
            actor_id=actor_id,
            rationale="Would leak identical bytes.",
        )


def test_adjudicated_cases_follow_explicit_states_and_concurrency_tokens() -> None:
    evaluation, _, clock = evaluation_fixture()
    fixture = create_reviewed_case(
        evaluation,
        public_artifact("d"),
        case_key="paper-d:figure-1",
        adjudicated=True,
    )
    service = GoldenDatasetService(
        InMemoryGoldenDatasetRepository(),
        evaluation,
        InMemoryArtifactLookup((fixture.artifact,)),
        clock=clock,
    )
    actor_id = uuid4()
    dataset = service.create_dataset(
        dataset_name="adjudicated-gold",
        dataset_version="1.0",
        dataset_type=GoldenDatasetType.CHALLENGE,
        predecessor_snapshot_id=None,
        created_by=actor_id,
    )
    detail = service.add_case(
        dataset.dataset_id,
        fixture.case_id,
        expected_dataset_revision=1,
        paper_key="doi:paper-d",
        split=GoldenDatasetSplit.TEST,
        actor_id=actor_id,
        rationale="Adjudicated challenge case.",
    )
    detail = _promote(
        service, detail, fixture.case_id, GoldenCaseState.PREDICTED, actor_id=actor_id
    )
    with pytest.raises(ConcurrencyConflict):
        service.promote_case(
            dataset.dataset_id,
            fixture.case_id,
            expected_dataset_revision=2,
            expected_state_version=1,
            target_state=GoldenCaseState.REVIEWED,
            selected_revision_id=fixture.selected_revision_id,
            actor_id=actor_id,
            rationale="Stale writer.",
        )
    detail = _promote(
        service,
        detail,
        fixture.case_id,
        GoldenCaseState.REVIEWED,
        selected_revision_id=fixture.selected_revision_id,
        actor_id=actor_id,
    )
    detail = _promote(
        service,
        detail,
        fixture.case_id,
        GoldenCaseState.NEEDS_ADJUDICATION,
        actor_id=actor_id,
    )
    detail = _promote(
        service,
        detail,
        fixture.case_id,
        GoldenCaseState.ADJUDICATED,
        selected_revision_id=fixture.selected_revision_id,
        actor_id=actor_id,
    )
    detail = _promote(
        service,
        detail,
        fixture.case_id,
        GoldenCaseState.GOLD_CANDIDATE,
        actor_id=actor_id,
    )
    detail = _promote(service, detail, fixture.case_id, GoldenCaseState.GOLD, actor_id=actor_id)
    snapshot = service.freeze(
        dataset.dataset_id,
        expected_dataset_revision=detail.dataset.revision,
        frozen_by=actor_id,
    )
    assert snapshot.cases[0].adjudication is not None
    assert snapshot.cases[0].provenance.adjudication_id is not None


def test_label_change_uses_successor_version_and_draft_deletion_preserves_snapshots() -> None:
    evaluation, _, clock = evaluation_fixture()
    fixture = create_reviewed_case(
        evaluation,
        public_artifact("e"),
        case_key="paper-e:figure-1",
    )
    repository = InMemoryGoldenDatasetRepository()
    service = GoldenDatasetService(
        repository,
        evaluation,
        InMemoryArtifactLookup((fixture.artifact,)),
        clock=clock,
    )
    actor_id = uuid4()
    first_dataset = service.create_dataset(
        dataset_name="versioned-gold",
        dataset_version="1.0",
        dataset_type=GoldenDatasetType.FROZEN_TEST,
        predecessor_snapshot_id=None,
        created_by=actor_id,
    )
    first_detail = service.add_case(
        first_dataset.dataset_id,
        fixture.case_id,
        expected_dataset_revision=1,
        paper_key="doi:paper-e",
        split=GoldenDatasetSplit.TEST,
        actor_id=actor_id,
        rationale="Initial label.",
    )
    first_detail = _promote_reviewed_case_to_gold(service, first_detail, fixture, actor_id)
    first_snapshot = service.freeze(
        first_dataset.dataset_id,
        expected_dataset_revision=first_detail.dataset.revision,
        frozen_by=actor_id,
    )

    revision = evaluation.get_revision(fixture.selected_revision_id)
    document = evaluation.get_annotation(revision.annotation_id)
    _, corrected = evaluation.append_revision(
        document.annotation_id,
        expected_head_revision_id=document.head_revision_id,
        reviewer_id=document.reviewer_id,
        rationale="Correct target identity.",
        error_codes=(),
        field_annotations=(),
        spatial_annotations=(),
        relationships=(),
        structured_annotation=structured_annotation("AKT1"),
    )
    second_fixture = ReviewedCaseFixture(
        case_id=fixture.case_id,
        selected_revision_id=corrected.revision_id,
        artifact=fixture.artifact,
    )
    second_dataset = service.create_dataset(
        dataset_name="versioned-gold",
        dataset_version="2.0",
        dataset_type=GoldenDatasetType.FROZEN_TEST,
        predecessor_snapshot_id=first_snapshot.snapshot_id,
        created_by=actor_id,
    )
    second_detail = service.add_case(
        second_dataset.dataset_id,
        fixture.case_id,
        expected_dataset_revision=1,
        paper_key="doi:paper-e",
        split=GoldenDatasetSplit.TEST,
        actor_id=actor_id,
        rationale="Corrected label version.",
    )
    second_detail = _promote_reviewed_case_to_gold(service, second_detail, second_fixture, actor_id)
    second_snapshot = service.freeze(
        second_dataset.dataset_id,
        expected_dataset_revision=second_detail.dataset.revision,
        frozen_by=actor_id,
    )
    change = second_snapshot.changelog.changes[0]
    assert change.change_type.value == "modified"
    assert "annotation_revision" in {item.value for item in change.changed_fields}
    assert change.previous_revision_id == fixture.selected_revision_id
    assert change.current_revision_id == corrected.revision_id

    abandoned = service.create_dataset(
        dataset_name="versioned-gold",
        dataset_version="3.0-draft",
        dataset_type=GoldenDatasetType.FROZEN_TEST,
        predecessor_snapshot_id=second_snapshot.snapshot_id,
        created_by=actor_id,
    )
    service.delete_draft(abandoned.dataset_id, expected_revision=abandoned.revision)
    assert service.get_snapshot(first_snapshot.snapshot_id) == first_snapshot
    assert service.get_snapshot(second_snapshot.snapshot_id) == second_snapshot


def _promote_reviewed_case_to_gold(
    service: GoldenDatasetService,
    detail,
    fixture: ReviewedCaseFixture,
    actor_id: UUID,
):
    detail = _promote(
        service, detail, fixture.case_id, GoldenCaseState.PREDICTED, actor_id=actor_id
    )
    detail = _promote(
        service,
        detail,
        fixture.case_id,
        GoldenCaseState.REVIEWED,
        selected_revision_id=fixture.selected_revision_id,
        actor_id=actor_id,
    )
    detail = _promote(
        service,
        detail,
        fixture.case_id,
        GoldenCaseState.GOLD_CANDIDATE,
        actor_id=actor_id,
    )
    return _promote(service, detail, fixture.case_id, GoldenCaseState.GOLD, actor_id=actor_id)


def _promote(
    service: GoldenDatasetService,
    detail,
    case_id: UUID,
    target_state: GoldenCaseState,
    *,
    actor_id: UUID,
    selected_revision_id: UUID | None = None,
):
    member = next(item for item in detail.members if item.case_id == case_id)
    return service.promote_case(
        detail.dataset.dataset_id,
        case_id,
        expected_dataset_revision=detail.dataset.revision,
        expected_state_version=member.state_version,
        target_state=target_state,
        selected_revision_id=selected_revision_id,
        actor_id=actor_id,
        rationale=f"Promote to {target_state.value}.",
    )
