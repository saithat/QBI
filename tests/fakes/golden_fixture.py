from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from uuid import UUID, uuid4

from hiveblot_contracts import (
    ArtifactAcquisitionMethod,
    ArtifactRecord,
    ArtifactVisibility,
    CaseArtifactRole,
    CaseSourceArtifact,
    ModelIdentifier,
    ReviewStatus,
)
from hiveblot_evaluation import EvaluationService

from tests.fakes.evaluation import InMemoryEvaluationRepository
from tests.fakes.structured import structured_annotation

NOW = datetime(2026, 8, 3, tzinfo=UTC)


class TickingClock:
    def __init__(self, value: datetime = NOW) -> None:
        self.value = value

    def __call__(self) -> datetime:
        current = self.value
        self.value += timedelta(seconds=1)
        return current


@dataclass(frozen=True)
class ReviewedCaseFixture:
    case_id: UUID
    selected_revision_id: UUID
    artifact: ArtifactRecord


def evaluation_fixture() -> tuple[EvaluationService, InMemoryEvaluationRepository, TickingClock]:
    repository = InMemoryEvaluationRepository()
    clock = TickingClock()
    return EvaluationService(repository, clock=clock), repository, clock


def public_artifact(
    seed: str,
    *,
    artifact_id: UUID | None = None,
    filename: str | None = None,
) -> ArtifactRecord:
    return ArtifactRecord(
        artifact_id=artifact_id or uuid4(),
        sha256=seed * 64,
        media_type="image/png",
        byte_size=256,
        original_filename=filename or f"figure-{seed}.png",
        source_uri=f"https://papers.test/{seed}/figure.png",
        acquisition_method=ArtifactAcquisitionMethod.SOURCE_ADAPTER,
        visibility=ArtifactVisibility.PUBLIC,
        created_at=NOW,
    )


def create_reviewed_case(
    evaluation: EvaluationService,
    artifact: ArtifactRecord,
    *,
    case_key: str,
    adjudicated: bool = False,
) -> ReviewedCaseFixture:
    case = evaluation.create_case(
        case_key=case_key,
        dataset_id=None,
        source_artifacts=(
            CaseSourceArtifact(
                artifact_id=artifact.artifact_id,
                role=CaseArtifactRole.FIGURE,
                page_number=1,
            ),
        ),
    )
    evaluation.add_prediction(
        case.case_id,
        prediction_schema="western-blot-structured-annotation",
        prediction_schema_version="1.0",
        producer=ModelIdentifier(provider="local", name="blot-model", version="1.0"),
        pipeline=None,
        raw_output_json='{"target":"TP53"}',
        normalized_output_json='{"target":"TP53"}',
        configuration_json="{}",
        evidence=(),
        validation_issues=(),
        confidence=0.9,
        trace_id=uuid4(),
        latency_ms=4,
        cost_microusd=0,
    )
    _, first = evaluation.create_annotation(
        case.case_id,
        reviewer_id=uuid4(),
        rationale="Reviewed source evidence.",
        error_codes=(),
        field_annotations=(),
        spatial_annotations=(),
        relationships=(),
        structured_annotation=structured_annotation("TP53"),
    )
    if not adjudicated:
        current = evaluation.get_case(case.case_id)
        evaluation.update_case_status(
            case.case_id,
            expected_version=current.version,
            review_status=ReviewStatus.REVIEWED,
        )
        return ReviewedCaseFixture(
            case_id=case.case_id,
            selected_revision_id=first.revision_id,
            artifact=artifact,
        )

    _, second = evaluation.create_annotation(
        case.case_id,
        reviewer_id=uuid4(),
        rationale="Independent review.",
        error_codes=(),
        field_annotations=(),
        spatial_annotations=(),
        relationships=(),
        structured_annotation=structured_annotation("TP53"),
    )
    current = evaluation.get_case(case.case_id)
    evaluation.update_case_status(
        case.case_id,
        expected_version=current.version,
        review_status=ReviewStatus.NEEDS_ADJUDICATION,
    )
    adjudication = evaluation.adjudicate(
        case.case_id,
        adjudicator_id=uuid4(),
        selected_revision_id=second.revision_id,
        considered_revision_ids=(first.revision_id, second.revision_id),
        rationale="The second review best reflects the source.",
    )
    return ReviewedCaseFixture(
        case_id=case.case_id,
        selected_revision_id=adjudication.selected_revision_id,
        artifact=artifact,
    )
