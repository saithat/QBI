"""Evaluation and immutable annotation-revision domain service."""

from __future__ import annotations

import json
from collections.abc import Callable, Sequence
from datetime import UTC, datetime
from uuid import UUID, uuid4

from hiveblot_contracts import (
    AdjudicationRecord,
    AnnotationDocumentRecord,
    AnnotationErrorCode,
    AnnotationRelationship,
    AnnotationRevision,
    CaseSourceArtifact,
    EvaluationCaseRecord,
    FieldAnnotation,
    PipelineIdentifier,
    PredictionDocument,
    PredictionEvidence,
    ProducerIdentifier,
    ReviewerAssignment,
    ReviewerAssignmentStatus,
    ReviewStatus,
    SpatialAnnotation,
    ValidationIssue,
    WesternBlotStructuredAnnotation,
)

from .errors import EvaluationNotFound, InvalidEvaluationState
from .repository import EvaluationRepository

CASE_STATUS_TRANSITIONS = {
    ReviewStatus.UNREVIEWED: {ReviewStatus.IN_REVIEW},
    ReviewStatus.IN_REVIEW: {ReviewStatus.REVIEWED, ReviewStatus.NEEDS_ADJUDICATION},
    ReviewStatus.REVIEWED: {ReviewStatus.NEEDS_ADJUDICATION},
    ReviewStatus.NEEDS_ADJUDICATION: {ReviewStatus.ADJUDICATED},
    ReviewStatus.ADJUDICATED: set(),
}


class EvaluationService:
    def __init__(
        self,
        repository: EvaluationRepository,
        *,
        clock: Callable[[], datetime] | None = None,
    ) -> None:
        self._repository = repository
        self._clock = clock or (lambda: datetime.now(UTC))

    def create_case(
        self,
        *,
        case_key: str,
        dataset_id: UUID | None,
        source_artifacts: tuple[CaseSourceArtifact, ...],
    ) -> EvaluationCaseRecord:
        if not source_artifacts:
            raise InvalidEvaluationState("an evaluation case requires at least one source artifact")
        identities = {(source.artifact_id, source.role) for source in source_artifacts}
        if len(identities) != len(source_artifacts):
            raise InvalidEvaluationState("case source artifact identities must be unique")
        now = self._clock()
        return self._repository.create_case(
            EvaluationCaseRecord(
                case_id=uuid4(),
                case_key=case_key,
                dataset_id=dataset_id,
                review_status=ReviewStatus.UNREVIEWED,
                version=1,
                source_artifacts=source_artifacts,
                created_at=now,
                updated_at=now,
            )
        )

    def get_case(self, case_id: UUID) -> EvaluationCaseRecord:
        record = self._repository.get_case(case_id)
        if record is None:
            raise EvaluationNotFound(f"evaluation case {case_id} does not exist")
        return record

    def update_case_status(
        self,
        case_id: UUID,
        *,
        expected_version: int,
        review_status: ReviewStatus,
    ) -> EvaluationCaseRecord:
        current = self.get_case(case_id)
        if review_status not in CASE_STATUS_TRANSITIONS[current.review_status]:
            raise InvalidEvaluationState(
                f"cannot move case from {current.review_status.value} to {review_status.value}"
            )
        return self._repository.update_case_status(
            case_id,
            expected_version=expected_version,
            review_status=review_status,
            updated_at=self._clock(),
        )

    def add_prediction(
        self,
        case_id: UUID,
        *,
        prediction_schema: str,
        prediction_schema_version: str,
        producer: ProducerIdentifier,
        pipeline: PipelineIdentifier | None,
        raw_output_json: str,
        normalized_output_json: str | None,
        configuration_json: str,
        evidence: tuple[PredictionEvidence, ...],
        validation_issues: tuple[ValidationIssue, ...],
        confidence: float | None,
        trace_id: UUID,
        latency_ms: int,
        cost_microusd: int,
    ) -> PredictionDocument:
        case = self.get_case(case_id)
        source_artifact_ids = {source.artifact_id for source in case.source_artifacts}
        referenced_artifact_ids = {item.artifact_id for item in evidence}
        referenced_artifact_ids.update(
            artifact_id
            for issue in validation_issues
            for artifact_id in issue.evidence_artifact_ids
        )
        if not referenced_artifact_ids.issubset(source_artifact_ids):
            raise InvalidEvaluationState("prediction evidence must reference case source artifacts")
        _validate_json_object(configuration_json, "configuration_json")
        if normalized_output_json is not None:
            _validate_json_document(normalized_output_json, "normalized_output_json")
        document = PredictionDocument(
            prediction_id=uuid4(),
            case_id=case_id,
            prediction_schema=prediction_schema,
            prediction_schema_version=prediction_schema_version,
            producer=producer,
            pipeline=pipeline,
            raw_output_json=raw_output_json,
            normalized_output_json=normalized_output_json,
            configuration_json=configuration_json,
            evidence=evidence,
            validation_issues=validation_issues,
            confidence=confidence,
            trace_id=trace_id,
            latency_ms=latency_ms,
            cost_microusd=cost_microusd,
            created_at=self._clock(),
        )
        return self._repository.add_prediction(document)

    def get_prediction(self, prediction_id: UUID) -> PredictionDocument:
        document = self._repository.get_prediction(prediction_id)
        if document is None:
            raise EvaluationNotFound(f"prediction {prediction_id} does not exist")
        return document

    def list_predictions(self, case_id: UUID) -> Sequence[PredictionDocument]:
        self.get_case(case_id)
        return self._repository.list_predictions(case_id)

    def create_annotation(
        self,
        case_id: UUID,
        *,
        reviewer_id: UUID,
        rationale: str | None,
        error_codes: tuple[str, ...],
        field_annotations: tuple[FieldAnnotation, ...],
        spatial_annotations: tuple[SpatialAnnotation, ...],
        relationships: tuple[AnnotationRelationship, ...],
        structured_annotation: WesternBlotStructuredAnnotation | None = None,
    ) -> tuple[AnnotationDocumentRecord, AnnotationRevision]:
        self.get_case(case_id)
        self._validate_error_codes(error_codes)
        self._validate_spatial_sources(case_id, spatial_annotations)
        now = self._clock()
        annotation_id = uuid4()
        revision_id = uuid4()
        revision = AnnotationRevision(
            revision_id=revision_id,
            annotation_id=annotation_id,
            revision_number=1,
            prior_revision_id=None,
            reviewer_id=reviewer_id,
            rationale=rationale,
            error_codes=error_codes,
            field_annotations=field_annotations,
            spatial_annotations=spatial_annotations,
            relationships=relationships,
            structured_annotation=structured_annotation,
            created_at=now,
        )
        document = AnnotationDocumentRecord(
            annotation_id=annotation_id,
            case_id=case_id,
            reviewer_id=reviewer_id,
            head_revision_id=revision_id,
            revision_count=1,
            created_at=now,
            updated_at=now,
        )
        return self._repository.create_annotation(document, revision), revision

    def append_revision(
        self,
        annotation_id: UUID,
        *,
        expected_head_revision_id: UUID,
        reviewer_id: UUID,
        rationale: str | None,
        error_codes: tuple[str, ...],
        field_annotations: tuple[FieldAnnotation, ...],
        spatial_annotations: tuple[SpatialAnnotation, ...],
        relationships: tuple[AnnotationRelationship, ...],
        structured_annotation: WesternBlotStructuredAnnotation | None = None,
    ) -> tuple[AnnotationDocumentRecord, AnnotationRevision]:
        document = self.get_annotation(annotation_id)
        if document.reviewer_id != reviewer_id:
            raise InvalidEvaluationState("only the document reviewer may append its revisions")
        self._validate_error_codes(error_codes)
        self._validate_spatial_sources(document.case_id, spatial_annotations)
        now = self._clock()
        revision = AnnotationRevision(
            revision_id=uuid4(),
            annotation_id=annotation_id,
            revision_number=document.revision_count + 1,
            prior_revision_id=expected_head_revision_id,
            reviewer_id=reviewer_id,
            rationale=rationale,
            error_codes=error_codes,
            field_annotations=field_annotations,
            spatial_annotations=spatial_annotations,
            relationships=relationships,
            structured_annotation=structured_annotation,
            created_at=now,
        )
        updated = self._repository.append_revision(
            revision,
            expected_head_revision_id=expected_head_revision_id,
            updated_at=now,
        )
        return updated, revision

    def get_annotation(self, annotation_id: UUID) -> AnnotationDocumentRecord:
        document = self._repository.get_annotation(annotation_id)
        if document is None:
            raise EvaluationNotFound(f"annotation {annotation_id} does not exist")
        return document

    def list_annotations(self, case_id: UUID) -> Sequence[AnnotationDocumentRecord]:
        self.get_case(case_id)
        return self._repository.list_annotations(case_id)

    def get_revision(self, revision_id: UUID) -> AnnotationRevision:
        revision = self._repository.get_revision(revision_id)
        if revision is None:
            raise EvaluationNotFound(f"annotation revision {revision_id} does not exist")
        return revision

    def list_revisions(self, annotation_id: UUID) -> Sequence[AnnotationRevision]:
        self.get_annotation(annotation_id)
        return self._repository.list_revisions(annotation_id)

    def list_error_codes(self, *, active_only: bool = True) -> Sequence[AnnotationErrorCode]:
        return self._repository.list_error_codes(active_only=active_only)

    def add_error_code(
        self,
        *,
        code: str,
        category: str,
        description: str,
    ) -> AnnotationErrorCode:
        return self._repository.add_error_code(
            AnnotationErrorCode(
                code=code,
                category=category,
                description=description,
                active=True,
                created_at=self._clock(),
            )
        )

    def assign_reviewer(
        self,
        case_id: UUID,
        *,
        reviewer_id: UUID,
        exclusive: bool,
    ) -> ReviewerAssignment:
        self.get_case(case_id)
        now = self._clock()
        return self._repository.create_assignment(
            ReviewerAssignment(
                assignment_id=uuid4(),
                case_id=case_id,
                reviewer_id=reviewer_id,
                exclusive=exclusive,
                status=ReviewerAssignmentStatus.ASSIGNED,
                version=1,
                assigned_at=now,
                updated_at=now,
            )
        )

    def update_assignment(
        self,
        assignment_id: UUID,
        *,
        expected_version: int,
        status: ReviewerAssignmentStatus,
    ) -> ReviewerAssignment:
        if status is ReviewerAssignmentStatus.ASSIGNED:
            raise InvalidEvaluationState("an assignment cannot transition back to assigned")
        return self._repository.update_assignment(
            assignment_id,
            expected_version=expected_version,
            status=status,
            updated_at=self._clock(),
        )

    def list_assignments(self, case_id: UUID) -> Sequence[ReviewerAssignment]:
        self.get_case(case_id)
        return self._repository.list_assignments(case_id)

    def adjudicate(
        self,
        case_id: UUID,
        *,
        adjudicator_id: UUID,
        selected_revision_id: UUID,
        considered_revision_ids: tuple[UUID, ...],
        rationale: str,
    ) -> AdjudicationRecord:
        case = self.get_case(case_id)
        if case.review_status is not ReviewStatus.NEEDS_ADJUDICATION:
            raise InvalidEvaluationState("case must need adjudication before it can be adjudicated")
        revisions = [self.get_revision(revision_id) for revision_id in considered_revision_ids]
        documents = [self.get_annotation(revision.annotation_id) for revision in revisions]
        if any(document.case_id != case_id for document in documents):
            raise InvalidEvaluationState("all adjudicated revisions must belong to the case")
        if len({document.reviewer_id for document in documents}) < 2:
            raise InvalidEvaluationState("adjudication requires revisions from two reviewers")
        record = AdjudicationRecord(
            adjudication_id=uuid4(),
            case_id=case_id,
            adjudicator_id=adjudicator_id,
            selected_revision_id=selected_revision_id,
            considered_revision_ids=considered_revision_ids,
            rationale=rationale,
            created_at=self._clock(),
        )
        return self._repository.create_adjudication(record)

    def list_adjudications(self, case_id: UUID) -> Sequence[AdjudicationRecord]:
        self.get_case(case_id)
        return self._repository.list_adjudications(case_id)

    def _validate_error_codes(self, error_codes: tuple[str, ...]) -> None:
        if len(error_codes) != len(set(error_codes)):
            raise InvalidEvaluationState("revision error codes must be unique")
        known = {record.code for record in self._repository.list_error_codes(active_only=True)}
        unknown = sorted(set(error_codes) - known)
        if unknown:
            raise InvalidEvaluationState("unknown or inactive error codes: " + ", ".join(unknown))

    def _validate_spatial_sources(
        self,
        case_id: UUID,
        spatial_annotations: tuple[SpatialAnnotation, ...],
    ) -> None:
        case = self.get_case(case_id)
        source_artifact_ids = {source.artifact_id for source in case.source_artifacts}
        if any(
            annotation.region.source_artifact_id not in source_artifact_ids
            for annotation in spatial_annotations
        ):
            raise InvalidEvaluationState("spatial annotations must reference case source artifacts")


def _validate_json_document(value: str, field_name: str) -> None:
    try:
        json.loads(value)
    except json.JSONDecodeError as exc:
        raise InvalidEvaluationState(f"{field_name} must contain valid JSON") from exc


def _validate_json_object(value: str, field_name: str) -> None:
    try:
        parsed = json.loads(value)
    except json.JSONDecodeError as exc:
        raise InvalidEvaluationState(f"{field_name} must contain valid JSON") from exc
    if not isinstance(parsed, dict):
        raise InvalidEvaluationState(f"{field_name} must contain a JSON object")
