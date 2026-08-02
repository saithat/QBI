from __future__ import annotations

from datetime import UTC, datetime
from uuid import UUID

from hiveblot_contracts import (
    AdjudicationRecord,
    AnnotationDocumentRecord,
    AnnotationErrorCode,
    AnnotationRevision,
    EvaluationCaseRecord,
    PredictionDocument,
    ReviewerAssignment,
    ReviewerAssignmentStatus,
    ReviewStatus,
)
from hiveblot_evaluation import (
    ConcurrencyConflict,
    DuplicateEvaluationRecord,
    EvaluationNotFound,
    InvalidEvaluationState,
)


class InMemoryEvaluationRepository:
    def __init__(self) -> None:
        self.cases: dict[UUID, EvaluationCaseRecord] = {}
        self.predictions: dict[UUID, PredictionDocument] = {}
        self.annotations: dict[UUID, AnnotationDocumentRecord] = {}
        self.revisions: dict[UUID, AnnotationRevision] = {}
        self.assignments: dict[UUID, ReviewerAssignment] = {}
        self.adjudications: dict[UUID, AdjudicationRecord] = {}
        created_at = datetime(2026, 8, 2, tzinfo=UTC)
        self.error_codes: dict[str, AnnotationErrorCode] = {
            "incorrect_value": AnnotationErrorCode(
                code="incorrect_value",
                category="field",
                description="Predicted field value is incorrect.",
                active=True,
                created_at=created_at,
            ),
            "incorrect_geometry": AnnotationErrorCode(
                code="incorrect_geometry",
                category="spatial",
                description="Predicted geometry is incorrect.",
                active=True,
                created_at=created_at,
            ),
        }

    def create_case(self, record: EvaluationCaseRecord) -> EvaluationCaseRecord:
        if any(item.case_key == record.case_key for item in self.cases.values()):
            raise DuplicateEvaluationRecord(record.case_key)
        self.cases[record.case_id] = record
        return record

    def get_case(self, case_id: UUID) -> EvaluationCaseRecord | None:
        return self.cases.get(case_id)

    def update_case_status(
        self,
        case_id: UUID,
        *,
        expected_version: int,
        review_status: ReviewStatus,
        updated_at: datetime,
    ) -> EvaluationCaseRecord:
        try:
            current = self.cases[case_id]
        except KeyError as exc:
            raise EvaluationNotFound(str(case_id)) from exc
        if current.version != expected_version:
            raise ConcurrencyConflict("case version changed")
        updated = current.model_copy(
            update={
                "review_status": review_status,
                "version": current.version + 1,
                "updated_at": updated_at,
            }
        )
        self.cases[case_id] = updated
        return updated

    def add_prediction(self, document: PredictionDocument) -> PredictionDocument:
        if document.prediction_id in self.predictions:
            raise DuplicateEvaluationRecord(str(document.prediction_id))
        self.predictions[document.prediction_id] = document
        return document

    def get_prediction(self, prediction_id: UUID) -> PredictionDocument | None:
        return self.predictions.get(prediction_id)

    def list_predictions(self, case_id: UUID):
        return tuple(item for item in self.predictions.values() if item.case_id == case_id)

    def create_annotation(
        self,
        document: AnnotationDocumentRecord,
        revision: AnnotationRevision,
    ) -> AnnotationDocumentRecord:
        if any(
            item.case_id == document.case_id and item.reviewer_id == document.reviewer_id
            for item in self.annotations.values()
        ):
            raise DuplicateEvaluationRecord("reviewer already reviewed case")
        self.annotations[document.annotation_id] = document
        self.revisions[revision.revision_id] = revision
        current = self.cases[document.case_id]
        if current.review_status is ReviewStatus.UNREVIEWED:
            self.cases[document.case_id] = current.model_copy(
                update={
                    "review_status": ReviewStatus.IN_REVIEW,
                    "version": current.version + 1,
                    "updated_at": document.updated_at,
                }
            )
        return document

    def append_revision(
        self,
        revision: AnnotationRevision,
        *,
        expected_head_revision_id: UUID,
        updated_at: datetime,
    ) -> AnnotationDocumentRecord:
        try:
            document = self.annotations[revision.annotation_id]
        except KeyError as exc:
            raise EvaluationNotFound(str(revision.annotation_id)) from exc
        if document.head_revision_id != expected_head_revision_id:
            raise ConcurrencyConflict("annotation head changed")
        if revision.prior_revision_id != expected_head_revision_id:
            raise InvalidEvaluationState("prior mismatch")
        self.revisions[revision.revision_id] = revision
        updated = document.model_copy(
            update={
                "head_revision_id": revision.revision_id,
                "revision_count": document.revision_count + 1,
                "updated_at": updated_at,
            }
        )
        self.annotations[document.annotation_id] = updated
        return updated

    def get_annotation(self, annotation_id: UUID) -> AnnotationDocumentRecord | None:
        return self.annotations.get(annotation_id)

    def list_annotations(self, case_id: UUID):
        return tuple(item for item in self.annotations.values() if item.case_id == case_id)

    def get_revision(self, revision_id: UUID) -> AnnotationRevision | None:
        return self.revisions.get(revision_id)

    def list_revisions(self, annotation_id: UUID):
        return tuple(
            sorted(
                (item for item in self.revisions.values() if item.annotation_id == annotation_id),
                key=lambda item: item.revision_number,
            )
        )

    def list_error_codes(self, *, active_only: bool = True):
        return tuple(item for item in self.error_codes.values() if item.active or not active_only)

    def add_error_code(self, record: AnnotationErrorCode) -> AnnotationErrorCode:
        if record.code in self.error_codes:
            raise DuplicateEvaluationRecord(record.code)
        self.error_codes[record.code] = record
        return record

    def create_assignment(self, assignment: ReviewerAssignment) -> ReviewerAssignment:
        active = [
            item
            for item in self.assignments.values()
            if item.case_id == assignment.case_id
            and item.status is ReviewerAssignmentStatus.ASSIGNED
        ]
        if any(item.reviewer_id == assignment.reviewer_id for item in active):
            raise ConcurrencyConflict("reviewer already assigned")
        if assignment.exclusive and active:
            raise ConcurrencyConflict("case already assigned")
        if any(item.exclusive for item in active):
            raise ConcurrencyConflict("case has exclusive assignment")
        self.assignments[assignment.assignment_id] = assignment
        return assignment

    def update_assignment(
        self,
        assignment_id: UUID,
        *,
        expected_version: int,
        status: ReviewerAssignmentStatus,
        updated_at: datetime,
    ) -> ReviewerAssignment:
        try:
            assignment = self.assignments[assignment_id]
        except KeyError as exc:
            raise EvaluationNotFound(str(assignment_id)) from exc
        if assignment.version != expected_version:
            raise ConcurrencyConflict("assignment version changed")
        updated = assignment.model_copy(
            update={
                "status": status,
                "version": assignment.version + 1,
                "updated_at": updated_at,
            }
        )
        self.assignments[assignment_id] = updated
        return updated

    def list_assignments(self, case_id: UUID):
        return tuple(item for item in self.assignments.values() if item.case_id == case_id)

    def create_adjudication(self, record: AdjudicationRecord) -> AdjudicationRecord:
        self.adjudications[record.adjudication_id] = record
        case = self.cases[record.case_id]
        self.cases[record.case_id] = case.model_copy(
            update={
                "review_status": ReviewStatus.ADJUDICATED,
                "version": case.version + 1,
                "updated_at": record.created_at,
            }
        )
        return record

    def list_adjudications(self, case_id: UUID):
        return tuple(item for item in self.adjudications.values() if item.case_id == case_id)
