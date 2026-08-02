"""Persistence protocol for reviewable evaluation data."""

from __future__ import annotations

from collections.abc import Sequence
from datetime import datetime
from typing import Protocol
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


class EvaluationRepository(Protocol):
    def create_case(self, record: EvaluationCaseRecord) -> EvaluationCaseRecord: ...

    def get_case(self, case_id: UUID) -> EvaluationCaseRecord | None: ...

    def update_case_status(
        self,
        case_id: UUID,
        *,
        expected_version: int,
        review_status: ReviewStatus,
        updated_at: datetime,
    ) -> EvaluationCaseRecord: ...

    def add_prediction(self, document: PredictionDocument) -> PredictionDocument: ...

    def get_prediction(self, prediction_id: UUID) -> PredictionDocument | None: ...

    def list_predictions(self, case_id: UUID) -> Sequence[PredictionDocument]: ...

    def create_annotation(
        self,
        document: AnnotationDocumentRecord,
        revision: AnnotationRevision,
    ) -> AnnotationDocumentRecord: ...

    def append_revision(
        self,
        revision: AnnotationRevision,
        *,
        expected_head_revision_id: UUID,
        updated_at: datetime,
    ) -> AnnotationDocumentRecord: ...

    def get_annotation(self, annotation_id: UUID) -> AnnotationDocumentRecord | None: ...

    def list_annotations(self, case_id: UUID) -> Sequence[AnnotationDocumentRecord]: ...

    def get_revision(self, revision_id: UUID) -> AnnotationRevision | None: ...

    def list_revisions(self, annotation_id: UUID) -> Sequence[AnnotationRevision]: ...

    def list_error_codes(self, *, active_only: bool = True) -> Sequence[AnnotationErrorCode]: ...

    def add_error_code(self, record: AnnotationErrorCode) -> AnnotationErrorCode: ...

    def create_assignment(self, assignment: ReviewerAssignment) -> ReviewerAssignment: ...

    def update_assignment(
        self,
        assignment_id: UUID,
        *,
        expected_version: int,
        status: ReviewerAssignmentStatus,
        updated_at: datetime,
    ) -> ReviewerAssignment: ...

    def list_assignments(self, case_id: UUID) -> Sequence[ReviewerAssignment]: ...

    def create_adjudication(self, record: AdjudicationRecord) -> AdjudicationRecord: ...

    def list_adjudications(self, case_id: UUID) -> Sequence[AdjudicationRecord]: ...
