from datetime import UTC, datetime
from uuid import uuid4

import pytest
from hiveblot_contracts import (
    ArtifactAcquisitionMethod,
    ArtifactRecord,
    ArtifactVisibility,
    CaseArtifactRole,
    CaseSourceArtifact,
)
from hiveblot_evaluation import (
    ConcurrencyConflict,
    EvaluationService,
    EvidenceWorkbenchService,
    InvalidEvaluationState,
)

from tests.fakes.evaluation import InMemoryEvaluationRepository
from tests.fakes.workbench import InMemoryArtifactLookup, InMemorySourceContextRepository

NOW = datetime(2026, 8, 2, 19, tzinfo=UTC)


def test_source_context_must_use_case_artifact_and_include_text() -> None:
    artifact = _artifact()
    evaluation = EvaluationService(InMemoryEvaluationRepository(), clock=lambda: NOW)
    case = evaluation.create_case(
        case_key="paper:figure-3:A",
        dataset_id=None,
        source_artifacts=(
            CaseSourceArtifact(artifact_id=artifact.artifact_id, role=CaseArtifactRole.FIGURE),
        ),
    )
    service = EvidenceWorkbenchService(
        evaluation,
        InMemoryArtifactLookup((artifact,)),
        InMemorySourceContextRepository(),
    )

    with pytest.raises(InvalidEvaluationState, match="case artifact association"):
        service.put_source_context(
            case.case_id,
            uuid4(),
            CaseArtifactRole.FIGURE,
            expected_head_revision_id=None,
            caption="wrong artifact",
            nearby_text=None,
        )
    with pytest.raises(InvalidEvaluationState, match="requires a caption"):
        service.put_source_context(
            case.case_id,
            artifact.artifact_id,
            CaseArtifactRole.FIGURE,
            expected_head_revision_id=None,
            caption="  ",
            nearby_text=None,
        )

    first = service.put_source_context(
        case.case_id,
        artifact.artifact_id,
        CaseArtifactRole.FIGURE,
        expected_head_revision_id=None,
        caption="Initial caption",
        nearby_text=None,
    )
    second = service.put_source_context(
        case.case_id,
        artifact.artifact_id,
        CaseArtifactRole.FIGURE,
        expected_head_revision_id=first.context_revision_id,
        caption="Corrected caption",
        nearby_text=None,
    )
    history = service.list_source_context_revisions(
        case.case_id,
        artifact.artifact_id,
        CaseArtifactRole.FIGURE,
    )
    assert history == (first, second)
    assert second.prior_revision_id == first.context_revision_id
    with pytest.raises(ConcurrencyConflict):
        service.put_source_context(
            case.case_id,
            artifact.artifact_id,
            CaseArtifactRole.FIGURE,
            expected_head_revision_id=first.context_revision_id,
            caption="Stale overwrite",
            nearby_text=None,
        )


def _artifact() -> ArtifactRecord:
    return ArtifactRecord(
        artifact_id=uuid4(),
        sha256="a" * 64,
        media_type="image/png",
        byte_size=2048,
        original_filename="figure-2.png",
        source_uri="https://repository.example/figure-2.png",
        acquisition_method=ArtifactAcquisitionMethod.SOURCE_ADAPTER,
        visibility=ArtifactVisibility.PUBLIC,
        created_at=NOW,
    )
