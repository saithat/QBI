"""Golden-dataset promotion, freezing, changelog, and export orchestration."""

from __future__ import annotations

from collections.abc import Callable, Sequence
from datetime import UTC, datetime, timedelta
from uuid import UUID, uuid4

from hiveblot_contracts import (
    AdjudicationRecord,
    ArtifactVisibility,
    GoldenCaseProvenance,
    GoldenCaseSourceArtifact,
    GoldenCaseState,
    GoldenCaseTransition,
    GoldenDatasetCaseChange,
    GoldenDatasetCaseSnapshot,
    GoldenDatasetChangedField,
    GoldenDatasetChangelog,
    GoldenDatasetChangeType,
    GoldenDatasetDraftDetail,
    GoldenDatasetExportObject,
    GoldenDatasetExportRecord,
    GoldenDatasetMember,
    GoldenDatasetRecord,
    GoldenDatasetSnapshot,
    GoldenDatasetSplit,
    GoldenDatasetStatus,
    GoldenDatasetType,
    ReviewStatus,
    golden_dataset_case_sha256,
    golden_dataset_content_sha256,
    golden_dataset_snapshot_id,
)

from .errors import ConcurrencyConflict, EvaluationNotFound, InvalidEvaluationState
from .golden_exports import GoldenDatasetExportPublisher
from .golden_repository import GoldenDatasetRepository
from .service import EvaluationService
from .workbench import ArtifactLookup

CASE_STATE_TRANSITIONS: dict[GoldenCaseState, frozenset[GoldenCaseState]] = {
    GoldenCaseState.UNLABELED: frozenset({GoldenCaseState.PREDICTED}),
    GoldenCaseState.PREDICTED: frozenset({GoldenCaseState.REVIEWED}),
    GoldenCaseState.REVIEWED: frozenset(
        {GoldenCaseState.NEEDS_ADJUDICATION, GoldenCaseState.GOLD_CANDIDATE}
    ),
    GoldenCaseState.NEEDS_ADJUDICATION: frozenset({GoldenCaseState.ADJUDICATED}),
    GoldenCaseState.ADJUDICATED: frozenset({GoldenCaseState.GOLD_CANDIDATE}),
    GoldenCaseState.GOLD_CANDIDATE: frozenset({GoldenCaseState.GOLD}),
    GoldenCaseState.GOLD: frozenset({GoldenCaseState.RETIRED}),
    GoldenCaseState.RETIRED: frozenset(),
}


class GoldenDatasetService:
    def __init__(
        self,
        repository: GoldenDatasetRepository,
        evaluation: EvaluationService,
        artifacts: ArtifactLookup,
        exporter: GoldenDatasetExportPublisher | None = None,
        *,
        signed_url_seconds: int = 900,
        clock: Callable[[], datetime] | None = None,
    ) -> None:
        self._repository = repository
        self._evaluation = evaluation
        self._artifacts = artifacts
        self._exporter = exporter
        self._signed_url_seconds = signed_url_seconds
        self._clock = clock or (lambda: datetime.now(UTC))

    def create_dataset(
        self,
        *,
        dataset_name: str,
        dataset_version: str,
        dataset_type: GoldenDatasetType,
        predecessor_snapshot_id: UUID | None,
        created_by: UUID,
    ) -> GoldenDatasetRecord:
        if predecessor_snapshot_id is not None:
            predecessor = self.get_snapshot(predecessor_snapshot_id)
            if predecessor.dataset_name != dataset_name:
                raise InvalidEvaluationState(
                    "a successor dataset must retain its predecessor dataset name"
                )
            if predecessor.dataset_type is not dataset_type:
                raise InvalidEvaluationState(
                    "a successor dataset must retain its predecessor dataset type"
                )
            if predecessor.dataset_version == dataset_version:
                raise InvalidEvaluationState("a successor dataset requires a new version")
        now = self._clock()
        return self._repository.create_dataset(
            GoldenDatasetRecord(
                dataset_id=uuid4(),
                dataset_name=dataset_name,
                dataset_version=dataset_version,
                dataset_type=dataset_type,
                status=GoldenDatasetStatus.DRAFT,
                predecessor_snapshot_id=predecessor_snapshot_id,
                revision=1,
                created_by=created_by,
                created_at=now,
                updated_at=now,
            )
        )

    def get_dataset(self, dataset_id: UUID) -> GoldenDatasetRecord:
        value = self._repository.get_dataset(dataset_id)
        if value is None:
            raise EvaluationNotFound(f"golden dataset {dataset_id} does not exist")
        return value

    def list_datasets(self, *, limit: int) -> Sequence[GoldenDatasetRecord]:
        if not 1 <= limit <= 200:
            raise InvalidEvaluationState("golden dataset limit must be between 1 and 200")
        return self._repository.list_datasets(limit=limit)

    def get_detail(self, dataset_id: UUID) -> GoldenDatasetDraftDetail:
        value = self._repository.get_detail(dataset_id)
        if value is None:
            raise EvaluationNotFound(f"golden dataset {dataset_id} does not exist")
        return value

    def delete_draft(self, dataset_id: UUID, *, expected_revision: int) -> None:
        dataset = self.get_dataset(dataset_id)
        _require_draft(dataset, expected_revision)
        self._repository.delete_draft(dataset_id, expected_revision=expected_revision)

    def add_case(
        self,
        dataset_id: UUID,
        case_id: UUID,
        *,
        expected_dataset_revision: int,
        paper_key: str,
        split: GoldenDatasetSplit,
        actor_id: UUID,
        rationale: str,
    ) -> GoldenDatasetDraftDetail:
        detail = self.get_detail(dataset_id)
        _require_draft(detail.dataset, expected_dataset_revision)
        if any(item.case_id == case_id for item in detail.members):
            raise InvalidEvaluationState(f"case {case_id} already belongs to the dataset")
        case = self._evaluation.get_case(case_id)
        if case.visibility is not ArtifactVisibility.PUBLIC:
            raise InvalidEvaluationState(
                "organization-private cases cannot enter a shared golden dataset"
            )
        sources = tuple(
            GoldenCaseSourceArtifact(
                role=source.role,
                page_number=source.page_number,
                artifact=self._artifacts.get_artifact(source.artifact_id),
            )
            for source in case.source_artifacts
        )
        if any(
            source.artifact.visibility is ArtifactVisibility.ORGANIZATION_PRIVATE
            for source in sources
        ):
            raise InvalidEvaluationState(
                "organization-private artifacts cannot enter a shared golden dataset"
            )
        now = self._clock()
        member = GoldenDatasetMember(
            dataset_id=dataset_id,
            case_id=case_id,
            paper_key=paper_key,
            split=split,
            state=GoldenCaseState.UNLABELED,
            selected_revision_id=None,
            state_version=1,
            added_by=actor_id,
            added_at=now,
            updated_at=now,
        )
        transition = GoldenCaseTransition(
            transition_id=uuid4(),
            dataset_id=dataset_id,
            case_id=case_id,
            from_state=None,
            to_state=GoldenCaseState.UNLABELED,
            selected_revision_id=None,
            actor_id=actor_id,
            rationale=rationale,
            created_at=now,
        )
        updated_dataset = _updated_dataset(detail.dataset, now=now)
        self._repository.add_member(
            member,
            sources,
            transition,
            expected_dataset_revision=expected_dataset_revision,
            updated_dataset=updated_dataset,
        )
        return self.get_detail(dataset_id)

    def promote_case(
        self,
        dataset_id: UUID,
        case_id: UUID,
        *,
        expected_dataset_revision: int,
        expected_state_version: int,
        target_state: GoldenCaseState,
        selected_revision_id: UUID | None,
        actor_id: UUID,
        rationale: str,
    ) -> GoldenDatasetDraftDetail:
        detail = self.get_detail(dataset_id)
        _require_draft(detail.dataset, expected_dataset_revision)
        member = next((item for item in detail.members if item.case_id == case_id), None)
        if member is None:
            raise EvaluationNotFound(
                f"case {case_id} is not a member of golden dataset {dataset_id}"
            )
        if member.state_version != expected_state_version:
            raise ConcurrencyConflict(
                f"golden member {case_id} no longer has state version {expected_state_version}"
            )
        if target_state not in CASE_STATE_TRANSITIONS[member.state]:
            raise InvalidEvaluationState(
                f"cannot move golden case from {member.state.value} to {target_state.value}"
            )
        selected = self._validate_promotion(
            case_id,
            member,
            target_state=target_state,
            selected_revision_id=selected_revision_id,
        )
        now = self._clock()
        updated_member = GoldenDatasetMember(
            dataset_id=member.dataset_id,
            case_id=member.case_id,
            paper_key=member.paper_key,
            split=member.split,
            state=target_state,
            selected_revision_id=selected,
            state_version=member.state_version + 1,
            added_by=member.added_by,
            added_at=member.added_at,
            updated_at=now,
        )
        transition = GoldenCaseTransition(
            transition_id=uuid4(),
            dataset_id=dataset_id,
            case_id=case_id,
            from_state=member.state,
            to_state=target_state,
            selected_revision_id=selected,
            actor_id=actor_id,
            rationale=rationale,
            created_at=now,
        )
        updated_dataset = _updated_dataset(detail.dataset, now=now)
        self._repository.transition_member(
            updated_member,
            transition,
            expected_dataset_revision=expected_dataset_revision,
            expected_state_version=expected_state_version,
            updated_dataset=updated_dataset,
        )
        return self.get_detail(dataset_id)

    def list_case_transitions(
        self,
        dataset_id: UUID,
        case_id: UUID,
    ) -> Sequence[GoldenCaseTransition]:
        self.get_dataset(dataset_id)
        if self._repository.get_member(dataset_id, case_id) is None:
            raise EvaluationNotFound(
                f"case {case_id} is not a member of golden dataset {dataset_id}"
            )
        return self._repository.list_transitions(dataset_id, case_id)

    def freeze(
        self,
        dataset_id: UUID,
        *,
        expected_dataset_revision: int,
        frozen_by: UUID,
    ) -> GoldenDatasetSnapshot:
        detail = self.get_detail(dataset_id)
        if detail.dataset.status is GoldenDatasetStatus.FROZEN:
            existing = self._repository.get_snapshot_by_dataset(dataset_id)
            if existing is None:
                raise InvalidEvaluationState("frozen dataset is missing its immutable snapshot")
            return existing
        _require_draft(detail.dataset, expected_dataset_revision)
        incomplete = [
            member
            for member in detail.members
            if member.state not in {GoldenCaseState.GOLD, GoldenCaseState.RETIRED}
        ]
        if incomplete:
            raise InvalidEvaluationState(
                "all non-retired dataset cases must be gold before freezing"
            )
        gold_members = tuple(
            member for member in detail.members if member.state is GoldenCaseState.GOLD
        )
        if not gold_members:
            raise InvalidEvaluationState("a frozen golden dataset requires at least one gold case")
        cases = tuple(
            sorted(
                (self._snapshot_case(member) for member in gold_members),
                key=lambda item: str(item.case_id),
            )
        )
        predecessor = (
            self.get_snapshot(detail.dataset.predecessor_snapshot_id)
            if detail.dataset.predecessor_snapshot_id is not None
            else None
        )
        changelog = build_golden_changelog(cases, predecessor)
        content_sha256 = golden_dataset_content_sha256(
            dataset_name=detail.dataset.dataset_name,
            dataset_version=detail.dataset.dataset_version,
            dataset_type=detail.dataset.dataset_type,
            predecessor_snapshot_id=detail.dataset.predecessor_snapshot_id,
            cases=cases,
            changelog=changelog,
        )
        now = self._clock()
        snapshot = GoldenDatasetSnapshot(
            snapshot_id=golden_dataset_snapshot_id(content_sha256),
            dataset_id=dataset_id,
            dataset_name=detail.dataset.dataset_name,
            dataset_version=detail.dataset.dataset_version,
            dataset_type=detail.dataset.dataset_type,
            predecessor_snapshot_id=detail.dataset.predecessor_snapshot_id,
            content_sha256=content_sha256,
            frozen_by=frozen_by,
            frozen_at=now,
            cases=cases,
            changelog=changelog,
        )
        updated_dataset = _updated_dataset(
            detail.dataset,
            now=now,
            status=GoldenDatasetStatus.FROZEN,
        )
        return self._repository.freeze(
            snapshot,
            updated_dataset,
            expected_dataset_revision=expected_dataset_revision,
        )

    def get_snapshot(self, snapshot_id: UUID) -> GoldenDatasetSnapshot:
        value = self._repository.get_snapshot(snapshot_id)
        if value is None:
            raise EvaluationNotFound(f"golden dataset snapshot {snapshot_id} does not exist")
        return value

    def publish_export(self, snapshot_id: UUID) -> GoldenDatasetExportRecord:
        existing = self._repository.get_export(snapshot_id)
        if existing is not None:
            return existing
        if self._exporter is None:
            raise InvalidEvaluationState("golden dataset export storage is not configured")
        record = self._exporter.publish(self.get_snapshot(snapshot_id))
        return self._repository.create_export(record)

    def get_export(self, snapshot_id: UUID) -> GoldenDatasetExportRecord:
        value = self._repository.get_export(snapshot_id)
        if value is None:
            raise EvaluationNotFound(f"golden dataset snapshot {snapshot_id} has no export")
        return value

    def create_export_download_url(
        self,
        snapshot_id: UUID,
        *,
        export_kind: str,
    ) -> tuple[str, datetime]:
        if self._exporter is None:
            raise InvalidEvaluationState("golden dataset export storage is not configured")
        record = self.get_export(snapshot_id)
        value: GoldenDatasetExportObject
        if export_kind == "cases_jsonl":
            value = record.cases_jsonl
        elif export_kind == "manifest_json":
            value = record.manifest_json
        else:
            raise InvalidEvaluationState("export kind must be cases_jsonl or manifest_json")
        expires_at = self._clock() + timedelta(seconds=self._signed_url_seconds)
        return (
            self._exporter.download_url(value, expires_in=self._signed_url_seconds),
            expires_at,
        )

    def _validate_promotion(
        self,
        case_id: UUID,
        member: GoldenDatasetMember,
        *,
        target_state: GoldenCaseState,
        selected_revision_id: UUID | None,
    ) -> UUID | None:
        case = self._evaluation.get_case(case_id)
        if target_state is GoldenCaseState.PREDICTED:
            if not self._evaluation.list_predictions(case_id):
                raise InvalidEvaluationState("predicted state requires at least one prediction")
            return None
        if target_state is GoldenCaseState.REVIEWED:
            if case.review_status not in {
                ReviewStatus.REVIEWED,
                ReviewStatus.NEEDS_ADJUDICATION,
                ReviewStatus.ADJUDICATED,
            }:
                raise InvalidEvaluationState("reviewed state requires completed scientific review")
            return self._reviewed_revision(case_id, selected_revision_id)
        if target_state is GoldenCaseState.NEEDS_ADJUDICATION:
            if case.review_status not in {
                ReviewStatus.NEEDS_ADJUDICATION,
                ReviewStatus.ADJUDICATED,
            }:
                raise InvalidEvaluationState(
                    "needs-adjudication state requires a case marked for adjudication"
                )
            return member.selected_revision_id
        if target_state is GoldenCaseState.ADJUDICATED:
            if case.review_status is not ReviewStatus.ADJUDICATED:
                raise InvalidEvaluationState("adjudicated state requires adjudicated case status")
            adjudication = self._latest_adjudication(case_id)
            if (
                selected_revision_id is not None
                and selected_revision_id != adjudication.selected_revision_id
            ):
                raise InvalidEvaluationState(
                    "selected revision must match the latest adjudication result"
                )
            return adjudication.selected_revision_id
        if target_state is GoldenCaseState.GOLD_CANDIDATE:
            if member.state is GoldenCaseState.REVIEWED:
                if case.review_status is not ReviewStatus.REVIEWED:
                    raise InvalidEvaluationState(
                        "cases awaiting or completing adjudication must follow adjudication states"
                    )
                selected = selected_revision_id or member.selected_revision_id
                self._validate_head_revision(case_id, selected)
                return selected
            if member.state is GoldenCaseState.ADJUDICATED:
                adjudication = self._latest_adjudication(case_id)
                selected = selected_revision_id or adjudication.selected_revision_id
                if selected != adjudication.selected_revision_id:
                    raise InvalidEvaluationState(
                        "gold candidate must retain the adjudicated revision"
                    )
                return selected
        if target_state is GoldenCaseState.GOLD:
            selected = selected_revision_id or member.selected_revision_id
            if selected is None:
                raise InvalidEvaluationState("gold state requires a selected revision")
            if case.review_status is ReviewStatus.ADJUDICATED:
                if selected != self._latest_adjudication(case_id).selected_revision_id:
                    raise InvalidEvaluationState("gold state must retain the adjudicated revision")
            else:
                self._validate_head_revision(case_id, selected)
            return selected
        if target_state is GoldenCaseState.RETIRED:
            return member.selected_revision_id
        raise InvalidEvaluationState(f"unsupported golden promotion target {target_state.value}")

    def _reviewed_revision(
        self,
        case_id: UUID,
        selected_revision_id: UUID | None,
    ) -> UUID:
        documents = tuple(
            document
            for document in self._evaluation.list_annotations(case_id)
            if document.visibility is ArtifactVisibility.PUBLIC
        )
        if not documents:
            raise InvalidEvaluationState("reviewed state requires an annotation revision")
        selected = selected_revision_id
        if selected is None:
            if len(documents) != 1:
                raise InvalidEvaluationState(
                    "multiple independent reviews require an explicit selected revision"
                )
            selected = documents[0].head_revision_id
        self._validate_head_revision(case_id, selected)
        return selected

    def _validate_head_revision(self, case_id: UUID, revision_id: UUID | None) -> None:
        if revision_id is None:
            raise InvalidEvaluationState("a selected annotation revision is required")
        revision = self._evaluation.get_revision(revision_id)
        document = self._evaluation.get_annotation(revision.annotation_id)
        if document.case_id != case_id:
            raise InvalidEvaluationState("selected annotation revision belongs to another case")
        if document.visibility is not ArtifactVisibility.PUBLIC:
            raise InvalidEvaluationState(
                "organization-private annotations cannot enter a shared golden dataset"
            )
        if document.head_revision_id != revision_id:
            raise InvalidEvaluationState(
                "selected reviewer revision must be a current document head"
            )

    def _latest_adjudication(self, case_id: UUID) -> AdjudicationRecord:
        records = tuple(
            record
            for record in self._evaluation.list_adjudications(case_id)
            if record.visibility is ArtifactVisibility.PUBLIC
        )
        if not records:
            raise InvalidEvaluationState("adjudicated state requires an adjudication record")
        return max(records, key=lambda item: (item.created_at, str(item.adjudication_id)))

    def _snapshot_case(self, member: GoldenDatasetMember) -> GoldenDatasetCaseSnapshot:
        if member.selected_revision_id is None:
            raise InvalidEvaluationState("gold member is missing its selected revision")
        case = self._evaluation.get_case(member.case_id)
        if case.visibility is not ArtifactVisibility.PUBLIC:
            raise InvalidEvaluationState(
                "organization-private cases cannot enter a shared frozen snapshot"
            )
        revision = self._evaluation.get_revision(member.selected_revision_id)
        document = self._evaluation.get_annotation(revision.annotation_id)
        if document.case_id != case.case_id:
            raise InvalidEvaluationState("gold revision no longer belongs to its evaluation case")
        if document.visibility is not ArtifactVisibility.PUBLIC:
            raise InvalidEvaluationState(
                "organization-private annotations cannot enter a shared frozen snapshot"
            )
        adjudication = next(
            (
                item
                for item in reversed(tuple(self._evaluation.list_adjudications(case.case_id)))
                if item.selected_revision_id == revision.revision_id
            ),
            None,
        )
        if adjudication is not None and adjudication.visibility is not ArtifactVisibility.PUBLIC:
            raise InvalidEvaluationState(
                "organization-private adjudication cannot enter a shared frozen snapshot"
            )
        transitions = tuple(self._repository.list_transitions(member.dataset_id, member.case_id))
        gold_transition = next(
            (item for item in reversed(transitions) if item.to_state is GoldenCaseState.GOLD),
            None,
        )
        if gold_transition is None:
            raise InvalidEvaluationState("gold member is missing its promotion provenance")
        source_artifacts = tuple(
            sorted(
                (
                    GoldenCaseSourceArtifact(
                        role=source.role,
                        page_number=source.page_number,
                        artifact=self._artifacts.get_artifact(source.artifact_id),
                    )
                    for source in case.source_artifacts
                ),
                key=lambda item: (item.role.value, str(item.artifact.artifact_id)),
            )
        )
        if any(
            source.artifact.visibility is ArtifactVisibility.ORGANIZATION_PRIVATE
            for source in source_artifacts
        ):
            raise InvalidEvaluationState(
                "organization-private artifacts cannot enter a shared frozen snapshot"
            )
        return GoldenDatasetCaseSnapshot(
            case_id=case.case_id,
            case_key=case.case_key,
            paper_key=member.paper_key,
            split=member.split,
            assay_type=case.assay_type,
            source_artifacts=source_artifacts,
            annotation_revision=revision,
            adjudication=adjudication,
            provenance=GoldenCaseProvenance(
                evaluation_case_version=case.version,
                review_status=case.review_status,
                prediction_ids=tuple(
                    sorted(
                        (
                            item.prediction_id
                            for item in self._evaluation.list_predictions(case.case_id)
                        ),
                        key=str,
                    )
                ),
                annotation_id=document.annotation_id,
                selected_revision_id=revision.revision_id,
                adjudication_id=(
                    adjudication.adjudication_id if adjudication is not None else None
                ),
                promoted_by=gold_transition.actor_id,
                promoted_at=gold_transition.created_at,
            ),
        )


def build_golden_changelog(
    current_cases: tuple[GoldenDatasetCaseSnapshot, ...],
    predecessor: GoldenDatasetSnapshot | None,
) -> GoldenDatasetChangelog:
    current = {item.case_id: item for item in current_cases}
    previous = {item.case_id: item for item in predecessor.cases} if predecessor else {}
    changes: list[GoldenDatasetCaseChange] = []
    for case_id in sorted(set(previous) | set(current), key=str):
        before = previous.get(case_id)
        after = current.get(case_id)
        if before is None and after is not None:
            changes.append(
                GoldenDatasetCaseChange(
                    case_id=case_id,
                    change_type=GoldenDatasetChangeType.ADDED,
                    changed_fields=(
                        GoldenDatasetChangedField.CASE_RECORD,
                        GoldenDatasetChangedField.SOURCE_ARTIFACTS,
                        GoldenDatasetChangedField.ANNOTATION_REVISION,
                    ),
                    current_case_sha256=golden_dataset_case_sha256(after),
                    current_split=after.split,
                    current_revision_id=after.annotation_revision.revision_id,
                )
            )
            continue
        if before is not None and after is None:
            changes.append(
                GoldenDatasetCaseChange(
                    case_id=case_id,
                    change_type=GoldenDatasetChangeType.REMOVED,
                    changed_fields=(GoldenDatasetChangedField.CASE_RECORD,),
                    previous_case_sha256=golden_dataset_case_sha256(before),
                    previous_split=before.split,
                    previous_revision_id=before.annotation_revision.revision_id,
                )
            )
            continue
        assert before is not None and after is not None
        previous_hash = golden_dataset_case_sha256(before)
        current_hash = golden_dataset_case_sha256(after)
        if previous_hash == current_hash:
            continue
        changed_fields: list[GoldenDatasetChangedField] = []
        if before.split is not after.split:
            changed_fields.append(GoldenDatasetChangedField.SPLIT)
        if before.annotation_revision.revision_id != after.annotation_revision.revision_id:
            changed_fields.append(GoldenDatasetChangedField.ANNOTATION_REVISION)
        if _artifact_identity(before) != _artifact_identity(after):
            changed_fields.append(GoldenDatasetChangedField.SOURCE_ARTIFACTS)
        if not changed_fields or _case_record_identity(before) != _case_record_identity(after):
            changed_fields.append(GoldenDatasetChangedField.CASE_RECORD)
        changes.append(
            GoldenDatasetCaseChange(
                case_id=case_id,
                change_type=GoldenDatasetChangeType.MODIFIED,
                changed_fields=tuple(changed_fields),
                previous_case_sha256=previous_hash,
                current_case_sha256=current_hash,
                previous_split=before.split,
                current_split=after.split,
                previous_revision_id=before.annotation_revision.revision_id,
                current_revision_id=after.annotation_revision.revision_id,
            )
        )
    return GoldenDatasetChangelog(
        predecessor_snapshot_id=(predecessor.snapshot_id if predecessor is not None else None),
        changes=tuple(changes),
    )


def _artifact_identity(case: GoldenDatasetCaseSnapshot) -> tuple[tuple[object, ...], ...]:
    return tuple(
        sorted(
            (
                source.role.value,
                source.page_number,
                str(source.artifact.artifact_id),
                source.artifact.sha256,
            )
            for source in case.source_artifacts
        )
    )


def _case_record_identity(case: GoldenDatasetCaseSnapshot) -> tuple[object, ...]:
    return (
        case.case_key,
        case.assay_type,
        case.provenance.evaluation_case_version,
        case.provenance.review_status,
        case.provenance.prediction_ids,
        case.provenance.adjudication_id,
    )


def _updated_dataset(
    value: GoldenDatasetRecord,
    *,
    now: datetime,
    status: GoldenDatasetStatus | None = None,
) -> GoldenDatasetRecord:
    return GoldenDatasetRecord(
        dataset_id=value.dataset_id,
        dataset_name=value.dataset_name,
        dataset_version=value.dataset_version,
        dataset_type=value.dataset_type,
        status=status or value.status,
        predecessor_snapshot_id=value.predecessor_snapshot_id,
        revision=value.revision + 1,
        created_by=value.created_by,
        created_at=value.created_at,
        updated_at=now,
    )


def _require_draft(dataset: GoldenDatasetRecord, expected_revision: int) -> None:
    if dataset.status is not GoldenDatasetStatus.DRAFT:
        raise InvalidEvaluationState("frozen golden datasets are immutable")
    if dataset.revision != expected_revision:
        raise ConcurrencyConflict(
            f"golden dataset {dataset.dataset_id} no longer has revision {expected_revision}"
        )
