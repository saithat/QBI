from uuid import UUID

import pytest
from hiveblot_evaluation import (
    InvalidEvaluationState,
    compare_metric_runs,
    score_evaluation,
)

from tests.fakes.metrics import CASE_ONE, CASE_TWO, NOW, scoring_input

BASELINE_RUN_ID = UUID("60000000-0000-0000-0000-000000000001")
CANDIDATE_RUN_ID = UUID("60000000-0000-0000-0000-000000000002")
COMPARISON_ID = UUID("70000000-0000-0000-0000-000000000001")


def test_scorer_reports_fields_geometry_citations_numbers_ranking_and_groups() -> None:
    result = score_evaluation(
        scoring_input(candidate=True),
        metric_run_id=CANDIDATE_RUN_ID,
        created_at=NOW,
    )

    assert result.overall.fields.exact.true_positive == 1
    assert result.overall.fields.normalized.true_positive == 1
    assert result.overall.fields.normalized.false_positive == 1
    assert result.overall.fields.normalized.false_negative == 1
    assert result.overall.geometry.match.true_positive == 1
    assert result.overall.geometry.comparable_regions == 2
    assert result.overall.relationships.true_positive == 1
    assert result.overall.citations.correct > 0
    assert result.overall.citations.completeness < 1
    assert result.overall.provenance.completeness < 1
    assert result.overall.numerical.comparable_values == 2
    assert result.overall.numerical.within_tolerance == 0.5
    assert result.overall.ranking.queries == 2
    assert result.overall.ranking.mean_reciprocal_rank == 0.5
    assert result.calibration.observations > 0
    assert 0 <= result.calibration.brier_score <= 1
    assert len(result.calibration.buckets) == 5
    assert len(result.calibration.coverage_curve) == 4
    assert {item.dimension.value for item in result.groups} >= {
        "source_type",
        "journal",
        "repository",
        "image_quality",
        "assay_layout",
        "review_status",
        "model_version",
        "pipeline_version",
    }
    assert result.cases[0].metrics.fields.normalized.true_positive == 1
    assert result.cases[1].metrics.fields.normalized.true_positive == 0


def test_pipeline_comparison_lists_individual_regressions_and_improvements() -> None:
    baseline = score_evaluation(
        scoring_input(),
        metric_run_id=BASELINE_RUN_ID,
        created_at=NOW,
    )
    candidate = score_evaluation(
        scoring_input(candidate=True),
        metric_run_id=CANDIDATE_RUN_ID,
        created_at=NOW,
    )
    comparison = compare_metric_runs(
        baseline,
        candidate,
        comparison_id=COMPARISON_ID,
        minimum_delta=0.01,
        created_at=NOW,
    )

    assert comparison.regressions == (CASE_TWO,)
    assert comparison.improvements == (CASE_ONE,)
    assert {item.case_id for item in comparison.cases} == {CASE_ONE, CASE_TWO}

    other_dataset = candidate.model_copy(update={"dataset_sha256": "f" * 64})
    with pytest.raises(InvalidEvaluationState, match="same frozen dataset"):
        compare_metric_runs(
            baseline,
            other_dataset,
            comparison_id=uuid_for_test(3),
            minimum_delta=0,
            created_at=NOW,
        )


def test_scoring_is_reproducible_for_explicit_identity_and_timestamp() -> None:
    fixture = scoring_input(candidate=True)
    first = score_evaluation(fixture, metric_run_id=CANDIDATE_RUN_ID, created_at=NOW)
    second = score_evaluation(fixture, metric_run_id=CANDIDATE_RUN_ID, created_at=NOW)

    assert first.model_dump_json() == second.model_dump_json()


def uuid_for_test(value: int) -> UUID:
    return UUID(f"80000000-0000-0000-0000-{value:012d}")
