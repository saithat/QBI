"""PostgreSQL mapping layer for immutable evaluation and annotation state."""

from __future__ import annotations

import json
from collections import defaultdict
from collections.abc import Sequence
from datetime import datetime
from typing import Any
from uuid import UUID

import psycopg
from hiveblot_contracts import (
    AdjudicationRecord,
    AnnotationDocumentRecord,
    AnnotationErrorCode,
    AnnotationRelationship,
    AnnotationRevision,
    BoundingRegion,
    CaseArtifactRole,
    CaseSourceArtifact,
    EntityFieldTarget,
    EvaluationCaseRecord,
    FieldAnnotation,
    FieldPathTarget,
    ObservationState,
    PipelineIdentifier,
    PredictionDocument,
    PredictionEvidence,
    ProducerIdentifier,
    ReviewerAssignment,
    ReviewerAssignmentStatus,
    ReviewStatus,
    SpatialAnnotation,
    SpatialAnnotationType,
    ValidationIssue,
    WesternBlotStructuredAnnotation,
)
from psycopg import errors
from psycopg.rows import dict_row
from psycopg.types.json import Jsonb
from pydantic import TypeAdapter

from .errors import (
    ConcurrencyConflict,
    DuplicateEvaluationRecord,
    EvaluationNotFound,
    InvalidEvaluationState,
)

PRODUCER_ADAPTER: TypeAdapter[ProducerIdentifier] = TypeAdapter(ProducerIdentifier)
EVIDENCE_ADAPTER = TypeAdapter(tuple[PredictionEvidence, ...])
ISSUES_ADAPTER = TypeAdapter(tuple[ValidationIssue, ...])


class PostgresEvaluationRepository:
    def __init__(self, database_url: str) -> None:
        self._database_url = database_url

    def create_case(self, record: EvaluationCaseRecord) -> EvaluationCaseRecord:
        try:
            with psycopg.connect(self._database_url, row_factory=dict_row) as connection:
                connection.execute(
                    """
                    INSERT INTO evaluation_cases (
                        case_id, case_key, dataset_id, assay_type, review_status,
                        version, created_at, updated_at
                    ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s)
                    """,
                    (
                        record.case_id,
                        record.case_key,
                        record.dataset_id,
                        record.assay_type,
                        record.review_status.value,
                        record.version,
                        record.created_at,
                        record.updated_at,
                    ),
                )
                for source in record.source_artifacts:
                    connection.execute(
                        """
                        INSERT INTO evaluation_case_artifacts (
                            case_id, artifact_id, artifact_role, page_number
                        ) VALUES (%s, %s, %s, %s)
                        """,
                        (record.case_id, source.artifact_id, source.role.value, source.page_number),
                    )
                created = self._get_case(connection, record.case_id)
                assert created is not None
                return created
        except errors.UniqueViolation as exc:
            raise DuplicateEvaluationRecord(
                f"evaluation case {record.case_key!r} already exists"
            ) from exc
        except errors.ForeignKeyViolation as exc:
            raise InvalidEvaluationState("case references an unknown artifact") from exc

    def get_case(self, case_id: UUID) -> EvaluationCaseRecord | None:
        with psycopg.connect(self._database_url, row_factory=dict_row) as connection:
            return self._get_case(connection, case_id)

    def get_case_by_key(self, case_key: str) -> EvaluationCaseRecord | None:
        with psycopg.connect(self._database_url, row_factory=dict_row) as connection:
            row = connection.execute(
                "SELECT case_id FROM evaluation_cases WHERE case_key = %s",
                (case_key,),
            ).fetchone()
            return self._get_case(connection, row["case_id"]) if row is not None else None

    def _get_case(self, connection: Any, case_id: UUID) -> EvaluationCaseRecord | None:
        row = connection.execute(
            "SELECT * FROM evaluation_cases WHERE case_id = %s",
            (case_id,),
        ).fetchone()
        if row is None:
            return None
        sources = connection.execute(
            """
            SELECT artifact_id, artifact_role, page_number
            FROM evaluation_case_artifacts
            WHERE case_id = %s
            ORDER BY artifact_role, artifact_id
            """,
            (case_id,),
        ).fetchall()
        return EvaluationCaseRecord(
            case_id=row["case_id"],
            case_key=row["case_key"],
            dataset_id=row["dataset_id"],
            assay_type="western_blot",
            review_status=ReviewStatus(row["review_status"]),
            version=row["version"],
            source_artifacts=tuple(
                CaseSourceArtifact(
                    artifact_id=source["artifact_id"],
                    role=CaseArtifactRole(source["artifact_role"]),
                    page_number=source["page_number"],
                )
                for source in sources
            ),
            created_at=row["created_at"],
            updated_at=row["updated_at"],
        )

    def update_case_status(
        self,
        case_id: UUID,
        *,
        expected_version: int,
        review_status: ReviewStatus,
        updated_at: datetime,
    ) -> EvaluationCaseRecord:
        with psycopg.connect(self._database_url, row_factory=dict_row) as connection:
            row = connection.execute(
                """
                UPDATE evaluation_cases
                SET review_status = %s, version = version + 1, updated_at = %s
                WHERE case_id = %s AND version = %s
                RETURNING case_id
                """,
                (review_status.value, updated_at, case_id, expected_version),
            ).fetchone()
            if row is None:
                if (
                    connection.execute(
                        "SELECT 1 FROM evaluation_cases WHERE case_id = %s", (case_id,)
                    ).fetchone()
                    is None
                ):
                    raise EvaluationNotFound(f"evaluation case {case_id} does not exist")
                raise ConcurrencyConflict(
                    f"evaluation case {case_id} no longer has version {expected_version}"
                )
            updated = self._get_case(connection, case_id)
            assert updated is not None
            return updated

    def add_prediction(self, document: PredictionDocument) -> PredictionDocument:
        try:
            with psycopg.connect(self._database_url) as connection:
                connection.execute(
                    """
                    INSERT INTO prediction_documents (
                        prediction_id, case_id, prediction_schema, prediction_schema_version,
                        producer_json, pipeline_json, raw_output_json, normalized_output_json,
                        configuration_json, evidence_json, validation_issues_json, confidence,
                        trace_id, latency_ms, cost_microusd, created_at
                    ) VALUES (
                        %s, %s, %s, %s, %s, %s, %s, %s,
                        %s, %s, %s, %s, %s, %s, %s, %s
                    )
                    """,
                    (
                        document.prediction_id,
                        document.case_id,
                        document.prediction_schema,
                        document.prediction_schema_version,
                        Jsonb(document.producer.model_dump(mode="json")),
                        (
                            Jsonb(document.pipeline.model_dump(mode="json"))
                            if document.pipeline is not None
                            else None
                        ),
                        document.raw_output_json,
                        document.normalized_output_json,
                        document.configuration_json,
                        Jsonb([item.model_dump(mode="json") for item in document.evidence]),
                        Jsonb(
                            [item.model_dump(mode="json") for item in document.validation_issues]
                        ),
                        document.confidence,
                        document.trace_id,
                        document.latency_ms,
                        document.cost_microusd,
                        document.created_at,
                    ),
                )
        except errors.UniqueViolation as exc:
            raise DuplicateEvaluationRecord(
                f"prediction {document.prediction_id} already exists"
            ) from exc
        except errors.ForeignKeyViolation as exc:
            raise EvaluationNotFound(f"evaluation case {document.case_id} does not exist") from exc
        return document

    def get_prediction(self, prediction_id: UUID) -> PredictionDocument | None:
        with psycopg.connect(self._database_url, row_factory=dict_row) as connection:
            row = connection.execute(
                "SELECT * FROM prediction_documents WHERE prediction_id = %s",
                (prediction_id,),
            ).fetchone()
        return _prediction_from_row(row) if row is not None else None

    def list_predictions(self, case_id: UUID) -> Sequence[PredictionDocument]:
        with psycopg.connect(self._database_url, row_factory=dict_row) as connection:
            rows = connection.execute(
                """
                SELECT * FROM prediction_documents
                WHERE case_id = %s ORDER BY created_at, prediction_id
                """,
                (case_id,),
            ).fetchall()
        return tuple(_prediction_from_row(row) for row in rows)

    def create_annotation(
        self,
        document: AnnotationDocumentRecord,
        revision: AnnotationRevision,
    ) -> AnnotationDocumentRecord:
        try:
            with psycopg.connect(self._database_url, row_factory=dict_row) as connection:
                connection.execute(
                    """
                    INSERT INTO annotation_documents (
                        annotation_id, case_id, reviewer_id, head_revision_id,
                        revision_count, created_at, updated_at
                    ) VALUES (%s, %s, %s, %s, %s, %s, %s)
                    """,
                    (
                        document.annotation_id,
                        document.case_id,
                        document.reviewer_id,
                        document.head_revision_id,
                        document.revision_count,
                        document.created_at,
                        document.updated_at,
                    ),
                )
                self._insert_revision(connection, revision)
                connection.execute(
                    """
                    UPDATE evaluation_cases
                    SET review_status = 'in_review', version = version + 1, updated_at = %s
                    WHERE case_id = %s AND review_status = 'unreviewed'
                    """,
                    (document.updated_at, document.case_id),
                )
                created = self._get_annotation(connection, document.annotation_id)
                assert created is not None
                return created
        except errors.UniqueViolation as exc:
            raise DuplicateEvaluationRecord(
                "this reviewer already has an annotation document for the case"
            ) from exc
        except errors.ForeignKeyViolation as exc:
            raise InvalidEvaluationState(
                "annotation references unknown case, artifact, or code"
            ) from exc
        except errors.CheckViolation as exc:
            raise InvalidEvaluationState(
                "annotation snapshot violates a database constraint"
            ) from exc

    def append_revision(
        self,
        revision: AnnotationRevision,
        *,
        expected_head_revision_id: UUID,
        updated_at: datetime,
    ) -> AnnotationDocumentRecord:
        try:
            with psycopg.connect(self._database_url, row_factory=dict_row) as connection:
                document_row = connection.execute(
                    """
                    SELECT * FROM annotation_documents
                    WHERE annotation_id = %s FOR UPDATE
                    """,
                    (revision.annotation_id,),
                ).fetchone()
                if document_row is None:
                    raise EvaluationNotFound(f"annotation {revision.annotation_id} does not exist")
                if document_row["head_revision_id"] != expected_head_revision_id:
                    raise ConcurrencyConflict(
                        "annotation head changed; reload before creating another revision"
                    )
                if revision.prior_revision_id != expected_head_revision_id:
                    raise InvalidEvaluationState("revision prior ID must match the expected head")
                if revision.reviewer_id != document_row["reviewer_id"]:
                    raise InvalidEvaluationState("revision reviewer does not own the document")
                if revision.revision_number != document_row["revision_count"] + 1:
                    raise InvalidEvaluationState("revision number is not the next sequence value")
                self._insert_revision(connection, revision)
                connection.execute(
                    """
                    UPDATE annotation_documents
                    SET head_revision_id = %s, revision_count = revision_count + 1,
                        updated_at = %s
                    WHERE annotation_id = %s
                    """,
                    (revision.revision_id, updated_at, revision.annotation_id),
                )
                updated = self._get_annotation(connection, revision.annotation_id)
                assert updated is not None
                return updated
        except (ConcurrencyConflict, EvaluationNotFound, InvalidEvaluationState):
            raise
        except errors.UniqueViolation as exc:
            raise DuplicateEvaluationRecord(
                f"annotation revision {revision.revision_id} already exists"
            ) from exc
        except errors.ForeignKeyViolation as exc:
            raise InvalidEvaluationState(
                "revision references an unknown artifact or error code"
            ) from exc
        except errors.CheckViolation as exc:
            raise InvalidEvaluationState(
                "annotation revision violates a database constraint"
            ) from exc

    def _insert_revision(self, connection: Any, revision: AnnotationRevision) -> None:
        connection.execute(
            """
            INSERT INTO annotation_revisions (
                revision_id, annotation_id, revision_number, prior_revision_id,
                reviewer_id, rationale, structured_annotation_json, created_at
            ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s)
            """,
            (
                revision.revision_id,
                revision.annotation_id,
                revision.revision_number,
                revision.prior_revision_id,
                revision.reviewer_id,
                revision.rationale,
                (
                    Jsonb(revision.structured_annotation.model_dump(mode="json"))
                    if revision.structured_annotation is not None
                    else None
                ),
                revision.created_at,
            ),
        )
        for spatial in revision.spatial_annotations:
            region = spatial.region
            connection.execute(
                """
                INSERT INTO spatial_annotations (
                    revision_id, spatial_annotation_id, annotation_type, observation_state,
                    source_artifact_id, region_id, x, y, width, height,
                    canvas_width, canvas_height, page_number, label
                ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
                """,
                (
                    revision.revision_id,
                    spatial.spatial_annotation_id,
                    spatial.annotation_type.value,
                    spatial.state.value,
                    region.source_artifact_id,
                    region.region_id,
                    region.x,
                    region.y,
                    region.width,
                    region.height,
                    region.canvas_width,
                    region.canvas_height,
                    region.page_number,
                    spatial.label,
                ),
            )
        for field in revision.field_annotations:
            if isinstance(field.target, FieldPathTarget):
                target_kind = "field_path"
                field_path = field.target.field_path
                entity_id = None
                field_name = None
            else:
                target_kind = "entity"
                field_path = None
                entity_id = field.target.entity_id
                field_name = field.target.field_name
            connection.execute(
                """
                INSERT INTO field_annotations (
                    revision_id, field_annotation_id, target_kind, field_path,
                    entity_id, field_name, observation_state, value_json,
                    original_extracted_text, notes
                ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
                """,
                (
                    revision.revision_id,
                    field.field_annotation_id,
                    target_kind,
                    field_path,
                    entity_id,
                    field_name,
                    field.state.value,
                    Jsonb(field.value) if field.value is not None else None,
                    field.original_extracted_text,
                    field.notes,
                ),
            )
            for region_id in field.evidence_region_ids:
                connection.execute(
                    """
                    INSERT INTO field_annotation_evidence (
                        revision_id, field_annotation_id, evidence_region_id
                    ) VALUES (%s, %s, %s)
                    """,
                    (revision.revision_id, field.field_annotation_id, region_id),
                )
        for relationship in revision.relationships:
            connection.execute(
                """
                INSERT INTO annotation_relationships (
                    revision_id, relationship_id, subject_id, relation_type, object_id
                ) VALUES (%s, %s, %s, %s, %s)
                """,
                (
                    revision.revision_id,
                    relationship.relationship_id,
                    relationship.subject_id,
                    relationship.relation_type,
                    relationship.object_id,
                ),
            )
        for code in revision.error_codes:
            connection.execute(
                """
                INSERT INTO annotation_revision_error_codes (revision_id, error_code)
                VALUES (%s, %s)
                """,
                (revision.revision_id, code),
            )

    def get_annotation(self, annotation_id: UUID) -> AnnotationDocumentRecord | None:
        with psycopg.connect(self._database_url, row_factory=dict_row) as connection:
            return self._get_annotation(connection, annotation_id)

    def _get_annotation(
        self, connection: Any, annotation_id: UUID
    ) -> AnnotationDocumentRecord | None:
        row = connection.execute(
            "SELECT * FROM annotation_documents WHERE annotation_id = %s",
            (annotation_id,),
        ).fetchone()
        return _annotation_from_row(row) if row is not None else None

    def list_annotations(self, case_id: UUID) -> Sequence[AnnotationDocumentRecord]:
        with psycopg.connect(self._database_url, row_factory=dict_row) as connection:
            rows = connection.execute(
                """
                SELECT * FROM annotation_documents
                WHERE case_id = %s ORDER BY created_at, annotation_id
                """,
                (case_id,),
            ).fetchall()
        return tuple(_annotation_from_row(row) for row in rows)

    def get_revision(self, revision_id: UUID) -> AnnotationRevision | None:
        with psycopg.connect(self._database_url, row_factory=dict_row) as connection:
            return self._get_revision(connection, revision_id)

    def _get_revision(self, connection: Any, revision_id: UUID) -> AnnotationRevision | None:
        row = connection.execute(
            "SELECT * FROM annotation_revisions WHERE revision_id = %s",
            (revision_id,),
        ).fetchone()
        if row is None:
            return None
        spatial_rows = connection.execute(
            """
            SELECT * FROM spatial_annotations
            WHERE revision_id = %s ORDER BY spatial_annotation_id
            """,
            (revision_id,),
        ).fetchall()
        field_rows = connection.execute(
            """
            SELECT * FROM field_annotations
            WHERE revision_id = %s ORDER BY field_annotation_id
            """,
            (revision_id,),
        ).fetchall()
        evidence_rows = connection.execute(
            """
            SELECT field_annotation_id, evidence_region_id
            FROM field_annotation_evidence
            WHERE revision_id = %s ORDER BY field_annotation_id, evidence_region_id
            """,
            (revision_id,),
        ).fetchall()
        evidence: defaultdict[UUID, list[UUID]] = defaultdict(list)
        for evidence_row in evidence_rows:
            evidence[evidence_row["field_annotation_id"]].append(evidence_row["evidence_region_id"])
        relationship_rows = connection.execute(
            """
            SELECT * FROM annotation_relationships
            WHERE revision_id = %s ORDER BY relationship_id
            """,
            (revision_id,),
        ).fetchall()
        error_rows = connection.execute(
            """
            SELECT error_code FROM annotation_revision_error_codes
            WHERE revision_id = %s ORDER BY error_code
            """,
            (revision_id,),
        ).fetchall()
        return AnnotationRevision(
            revision_id=row["revision_id"],
            annotation_id=row["annotation_id"],
            revision_number=row["revision_number"],
            prior_revision_id=row["prior_revision_id"],
            reviewer_id=row["reviewer_id"],
            rationale=row["rationale"],
            error_codes=tuple(item["error_code"] for item in error_rows),
            field_annotations=tuple(
                FieldAnnotation(
                    field_annotation_id=field["field_annotation_id"],
                    target=(
                        FieldPathTarget(field_path=field["field_path"])
                        if field["target_kind"] == "field_path"
                        else EntityFieldTarget(
                            entity_id=field["entity_id"],
                            field_name=field["field_name"],
                        )
                    ),
                    state=ObservationState(field["observation_state"]),
                    value=field["value_json"],
                    original_extracted_text=field["original_extracted_text"],
                    evidence_region_ids=tuple(evidence[field["field_annotation_id"]]),
                    notes=field["notes"],
                )
                for field in field_rows
            ),
            spatial_annotations=tuple(
                SpatialAnnotation(
                    spatial_annotation_id=spatial["spatial_annotation_id"],
                    annotation_type=SpatialAnnotationType(spatial["annotation_type"]),
                    state=ObservationState(spatial["observation_state"]),
                    region=BoundingRegion(
                        region_id=spatial["region_id"],
                        source_artifact_id=spatial["source_artifact_id"],
                        x=spatial["x"],
                        y=spatial["y"],
                        width=spatial["width"],
                        height=spatial["height"],
                        canvas_width=spatial["canvas_width"],
                        canvas_height=spatial["canvas_height"],
                        page_number=spatial["page_number"],
                    ),
                    label=spatial["label"],
                )
                for spatial in spatial_rows
            ),
            relationships=tuple(
                AnnotationRelationship(
                    relationship_id=relationship["relationship_id"],
                    subject_id=relationship["subject_id"],
                    relation_type=relationship["relation_type"],
                    object_id=relationship["object_id"],
                )
                for relationship in relationship_rows
            ),
            structured_annotation=(
                WesternBlotStructuredAnnotation.model_validate_json(
                    json.dumps(row["structured_annotation_json"])
                )
                if row["structured_annotation_json"] is not None
                else None
            ),
            created_at=row["created_at"],
        )

    def list_revisions(self, annotation_id: UUID) -> Sequence[AnnotationRevision]:
        with psycopg.connect(self._database_url, row_factory=dict_row) as connection:
            rows = connection.execute(
                """
                SELECT revision_id FROM annotation_revisions
                WHERE annotation_id = %s ORDER BY revision_number
                """,
                (annotation_id,),
            ).fetchall()
            revisions = [self._get_revision(connection, row["revision_id"]) for row in rows]
        return tuple(revision for revision in revisions if revision is not None)

    def list_error_codes(self, *, active_only: bool = True) -> Sequence[AnnotationErrorCode]:
        query = "SELECT * FROM annotation_error_codes"
        if active_only:
            query += " WHERE active = TRUE"
        query += " ORDER BY category, code"
        with psycopg.connect(self._database_url, row_factory=dict_row) as connection:
            rows = connection.execute(query).fetchall()
        return tuple(_error_code_from_row(row) for row in rows)

    def add_error_code(self, record: AnnotationErrorCode) -> AnnotationErrorCode:
        try:
            with psycopg.connect(self._database_url) as connection:
                connection.execute(
                    """
                    INSERT INTO annotation_error_codes (
                        code, category, description, active, created_at
                    ) VALUES (%s, %s, %s, %s, %s)
                    """,
                    (
                        record.code,
                        record.category,
                        record.description,
                        record.active,
                        record.created_at,
                    ),
                )
        except errors.UniqueViolation as exc:
            raise DuplicateEvaluationRecord(f"error code {record.code!r} already exists") from exc
        return record

    def create_assignment(self, assignment: ReviewerAssignment) -> ReviewerAssignment:
        try:
            with psycopg.connect(self._database_url, row_factory=dict_row) as connection:
                case = connection.execute(
                    "SELECT case_id FROM evaluation_cases WHERE case_id = %s FOR UPDATE",
                    (assignment.case_id,),
                ).fetchone()
                if case is None:
                    raise EvaluationNotFound(f"evaluation case {assignment.case_id} does not exist")
                active = connection.execute(
                    """
                    SELECT reviewer_id, exclusive FROM reviewer_assignments
                    WHERE case_id = %s AND assignment_status = 'assigned'
                    """,
                    (assignment.case_id,),
                ).fetchall()
                if any(row["reviewer_id"] == assignment.reviewer_id for row in active):
                    raise ConcurrencyConflict("reviewer already has an active case assignment")
                if (assignment.exclusive and active) or any(row["exclusive"] for row in active):
                    raise ConcurrencyConflict("case has a conflicting exclusive assignment")
                connection.execute(
                    """
                    INSERT INTO reviewer_assignments (
                        assignment_id, case_id, reviewer_id, exclusive, assignment_status,
                        version, assigned_at, updated_at
                    ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s)
                    """,
                    (
                        assignment.assignment_id,
                        assignment.case_id,
                        assignment.reviewer_id,
                        assignment.exclusive,
                        assignment.status.value,
                        assignment.version,
                        assignment.assigned_at,
                        assignment.updated_at,
                    ),
                )
        except (ConcurrencyConflict, EvaluationNotFound):
            raise
        except errors.UniqueViolation as exc:
            raise ConcurrencyConflict(
                "review assignment conflicts with an active assignment"
            ) from exc
        except errors.ForeignKeyViolation as exc:
            raise EvaluationNotFound(
                f"evaluation case {assignment.case_id} does not exist"
            ) from exc
        return assignment

    def update_assignment(
        self,
        assignment_id: UUID,
        *,
        expected_version: int,
        status: ReviewerAssignmentStatus,
        updated_at: datetime,
    ) -> ReviewerAssignment:
        with psycopg.connect(self._database_url, row_factory=dict_row) as connection:
            row = connection.execute(
                """
                UPDATE reviewer_assignments
                SET assignment_status = %s, version = version + 1, updated_at = %s
                WHERE assignment_id = %s AND version = %s AND assignment_status = 'assigned'
                RETURNING *
                """,
                (status.value, updated_at, assignment_id, expected_version),
            ).fetchone()
            if row is None:
                if (
                    connection.execute(
                        "SELECT 1 FROM reviewer_assignments WHERE assignment_id = %s",
                        (assignment_id,),
                    ).fetchone()
                    is None
                ):
                    raise EvaluationNotFound(f"assignment {assignment_id} does not exist")
                raise ConcurrencyConflict("assignment changed or is no longer active")
        return _assignment_from_row(row)

    def list_assignments(self, case_id: UUID) -> Sequence[ReviewerAssignment]:
        with psycopg.connect(self._database_url, row_factory=dict_row) as connection:
            rows = connection.execute(
                """
                SELECT * FROM reviewer_assignments
                WHERE case_id = %s ORDER BY assigned_at, assignment_id
                """,
                (case_id,),
            ).fetchall()
        return tuple(_assignment_from_row(row) for row in rows)

    def create_adjudication(self, record: AdjudicationRecord) -> AdjudicationRecord:
        try:
            with psycopg.connect(self._database_url, row_factory=dict_row) as connection:
                revision_rows = connection.execute(
                    """
                    SELECT r.revision_id, d.case_id
                    FROM annotation_revisions AS r
                    JOIN annotation_documents AS d ON d.annotation_id = r.annotation_id
                    WHERE r.revision_id = ANY(%s)
                    """,
                    (list(record.considered_revision_ids),),
                ).fetchall()
                if len(revision_rows) != len(record.considered_revision_ids):
                    raise InvalidEvaluationState("adjudication references an unknown revision")
                if any(row["case_id"] != record.case_id for row in revision_rows):
                    raise InvalidEvaluationState("adjudication revision belongs to another case")
                connection.execute(
                    """
                    INSERT INTO adjudication_records (
                        adjudication_id, case_id, adjudicator_id,
                        selected_revision_id, rationale, created_at
                    ) VALUES (%s, %s, %s, %s, %s, %s)
                    """,
                    (
                        record.adjudication_id,
                        record.case_id,
                        record.adjudicator_id,
                        record.selected_revision_id,
                        record.rationale,
                        record.created_at,
                    ),
                )
                for revision_id in record.considered_revision_ids:
                    connection.execute(
                        """
                        INSERT INTO adjudication_considered_revisions (
                            adjudication_id, revision_id
                        ) VALUES (%s, %s)
                        """,
                        (record.adjudication_id, revision_id),
                    )
                connection.execute(
                    """
                    UPDATE evaluation_cases
                    SET review_status = 'adjudicated', version = version + 1, updated_at = %s
                    WHERE case_id = %s
                    """,
                    (record.created_at, record.case_id),
                )
        except InvalidEvaluationState:
            raise
        except errors.UniqueViolation as exc:
            raise DuplicateEvaluationRecord(
                f"adjudication {record.adjudication_id} already exists"
            ) from exc
        except errors.ForeignKeyViolation as exc:
            raise InvalidEvaluationState(
                "adjudication references an unknown case or revision"
            ) from exc
        return record

    def list_adjudications(self, case_id: UUID) -> Sequence[AdjudicationRecord]:
        with psycopg.connect(self._database_url, row_factory=dict_row) as connection:
            rows = connection.execute(
                """
                SELECT * FROM adjudication_records
                WHERE case_id = %s ORDER BY created_at, adjudication_id
                """,
                (case_id,),
            ).fetchall()
            records = []
            for row in rows:
                considered = connection.execute(
                    """
                    SELECT revision_id FROM adjudication_considered_revisions
                    WHERE adjudication_id = %s ORDER BY revision_id
                    """,
                    (row["adjudication_id"],),
                ).fetchall()
                records.append(
                    AdjudicationRecord(
                        adjudication_id=row["adjudication_id"],
                        case_id=row["case_id"],
                        adjudicator_id=row["adjudicator_id"],
                        selected_revision_id=row["selected_revision_id"],
                        considered_revision_ids=tuple(item["revision_id"] for item in considered),
                        rationale=row["rationale"],
                        created_at=row["created_at"],
                    )
                )
        return tuple(records)


def _prediction_from_row(row: dict[str, Any]) -> PredictionDocument:
    return PredictionDocument(
        prediction_id=row["prediction_id"],
        case_id=row["case_id"],
        prediction_schema=row["prediction_schema"],
        prediction_schema_version=row["prediction_schema_version"],
        producer=PRODUCER_ADAPTER.validate_json(json.dumps(row["producer_json"])),
        pipeline=(
            PipelineIdentifier.model_validate_json(json.dumps(row["pipeline_json"]))
            if row["pipeline_json"] is not None
            else None
        ),
        raw_output_json=row["raw_output_json"],
        normalized_output_json=row["normalized_output_json"],
        configuration_json=row["configuration_json"],
        evidence=EVIDENCE_ADAPTER.validate_json(json.dumps(row["evidence_json"])),
        validation_issues=ISSUES_ADAPTER.validate_json(json.dumps(row["validation_issues_json"])),
        confidence=row["confidence"],
        trace_id=row["trace_id"],
        latency_ms=row["latency_ms"],
        cost_microusd=row["cost_microusd"],
        created_at=row["created_at"],
    )


def _annotation_from_row(row: dict[str, Any]) -> AnnotationDocumentRecord:
    return AnnotationDocumentRecord(
        annotation_id=row["annotation_id"],
        case_id=row["case_id"],
        reviewer_id=row["reviewer_id"],
        head_revision_id=row["head_revision_id"],
        revision_count=row["revision_count"],
        created_at=row["created_at"],
        updated_at=row["updated_at"],
    )


def _error_code_from_row(row: dict[str, Any]) -> AnnotationErrorCode:
    return AnnotationErrorCode(
        code=row["code"],
        category=row["category"],
        description=row["description"],
        active=row["active"],
        created_at=row["created_at"],
    )


def _assignment_from_row(row: dict[str, Any]) -> ReviewerAssignment:
    return ReviewerAssignment(
        assignment_id=row["assignment_id"],
        case_id=row["case_id"],
        reviewer_id=row["reviewer_id"],
        exclusive=row["exclusive"],
        status=ReviewerAssignmentStatus(row["assignment_status"]),
        version=row["version"],
        assigned_at=row["assigned_at"],
        updated_at=row["updated_at"],
    )
