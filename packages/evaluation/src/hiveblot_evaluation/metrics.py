"""Deterministic evaluation scoring, calibration, grouping, and comparison."""

from __future__ import annotations

import hashlib
import json
import math
from collections import defaultdict
from collections.abc import Callable, Iterable, Sequence
from datetime import UTC, datetime
from uuid import UUID, uuid4

from hiveblot_contracts import (
    CalibrationBucket,
    CalibrationCategory,
    CalibrationSlice,
    CalibrationSummary,
    CitationMetricSummary,
    ComparisonOutcome,
    ConfidenceOutcome,
    CoveragePoint,
    EvaluationCaseComparison,
    EvaluationCaseDimensions,
    EvaluationCaseMetricResult,
    EvaluationGroupDimension,
    EvaluationMetricConfiguration,
    EvaluationMetricGroupResult,
    EvaluationMetricRunRecord,
    EvaluationMetricSummary,
    EvaluationReferenceCase,
    EvaluationScoringInput,
    FieldMetricSummary,
    GeometryMetricSummary,
    MatchMetricSummary,
    NumericalMetricSummary,
    PipelineComparisonRecord,
    PipelineEvaluationCase,
    PredictedObservationDocument,
    PredictedRegionObservation,
    ProvenanceMetricSummary,
    RankingMetricSummary,
    ReferenceObservationDocument,
    ReferenceRegionObservation,
)

from .errors import EvaluationNotFound, InvalidEvaluationState
from .metrics_repository import EvaluationMetricRunRepository

Pair = tuple[EvaluationReferenceCase, PipelineEvaluationCase]


class EvaluationMetricsService:
    def __init__(
        self,
        repository: EvaluationMetricRunRepository,
        *,
        clock: Callable[[], datetime] | None = None,
        identity: Callable[[], UUID] | None = None,
    ) -> None:
        self._repository = repository
        self._clock = clock or (lambda: datetime.now(UTC))
        self._identity = identity or uuid4

    def score(self, scoring_input: EvaluationScoringInput) -> EvaluationMetricRunRecord:
        record = score_evaluation(
            scoring_input,
            metric_run_id=self._identity(),
            created_at=self._clock(),
        )
        return self._repository.create(record)

    def get(self, metric_run_id: UUID) -> EvaluationMetricRunRecord:
        record = self._repository.get(metric_run_id)
        if record is None:
            raise EvaluationNotFound(f"evaluation metric run {metric_run_id} does not exist")
        return record

    def list(
        self,
        *,
        dataset_name: str | None,
        dataset_version: str | None,
        pipeline_name: str | None,
        pipeline_version: str | None,
        limit: int,
    ) -> Sequence[EvaluationMetricRunRecord]:
        if not 1 <= limit <= 200:
            raise InvalidEvaluationState("metric run limit must be between 1 and 200")
        return self._repository.list(
            dataset_name=dataset_name,
            dataset_version=dataset_version,
            pipeline_name=pipeline_name,
            pipeline_version=pipeline_version,
            limit=limit,
        )

    def get_case(
        self,
        metric_run_id: UUID,
        case_id: UUID,
    ) -> EvaluationCaseMetricResult:
        run = self.get(metric_run_id)
        case = next((item for item in run.cases if item.case_id == case_id), None)
        if case is None:
            raise EvaluationNotFound(
                f"case {case_id} is not part of evaluation metric run {metric_run_id}"
            )
        return case

    def calibration(
        self,
        metric_run_id: UUID,
        *,
        category: CalibrationCategory | None,
    ) -> CalibrationSlice:
        run = self.get(metric_run_id)
        outcomes = tuple(
            outcome
            for case in run.cases
            for outcome in case.confidence_outcomes
            if category is None or outcome.category is category
        )
        return CalibrationSlice(
            metric_run_id=metric_run_id,
            category=category,
            calibration=_calibration(outcomes, run.configuration),
        )

    def compare(
        self,
        baseline_metric_run_id: UUID,
        candidate_metric_run_id: UUID,
        *,
        minimum_delta: float,
    ) -> PipelineComparisonRecord:
        if not 0 <= minimum_delta <= 1 or not math.isfinite(minimum_delta):
            raise InvalidEvaluationState("minimum comparison delta must be between zero and one")
        baseline = self.get(baseline_metric_run_id)
        candidate = self.get(candidate_metric_run_id)
        return compare_metric_runs(
            baseline,
            candidate,
            comparison_id=self._identity(),
            minimum_delta=minimum_delta,
            created_at=self._clock(),
        )


def score_evaluation(
    scoring_input: EvaluationScoringInput,
    *,
    metric_run_id: UUID,
    created_at: datetime,
) -> EvaluationMetricRunRecord:
    references = {item.case_id: item for item in scoring_input.dataset.cases}
    predictions = {item.case_id: item for item in scoring_input.submission.cases}
    pairs = tuple(
        (references[case_id], predictions[case_id]) for case_id in sorted(references, key=str)
    )
    configuration = scoring_input.configuration
    case_results = tuple(
        EvaluationCaseMetricResult(
            case_id=reference.case_id,
            dimensions=reference.dimensions,
            metrics=_score_scope(((reference, prediction),), configuration)[0],
            confidence_outcomes=_confidence_outcomes(reference, prediction, configuration),
        )
        for reference, prediction in pairs
    )
    overall, outcomes = _score_scope(pairs, configuration)
    groups = _group_results(pairs, scoring_input, configuration)
    return EvaluationMetricRunRecord(
        metric_run_id=metric_run_id,
        dataset_snapshot_id=scoring_input.dataset.snapshot_id,
        dataset_name=scoring_input.dataset.dataset_name,
        dataset_version=scoring_input.dataset.dataset_version,
        dataset_sha256=scoring_input.dataset.content_sha256,
        scorer=scoring_input.scorer,
        pipeline=scoring_input.submission.pipeline,
        model_versions=scoring_input.submission.model_versions,
        configuration=configuration,
        input_sha256=evaluation_scoring_input_sha256(scoring_input),
        overall=overall,
        calibration=_calibration(outcomes, configuration),
        groups=groups,
        cases=case_results,
        created_at=created_at,
    )


def compare_metric_runs(
    baseline: EvaluationMetricRunRecord,
    candidate: EvaluationMetricRunRecord,
    *,
    comparison_id: UUID,
    minimum_delta: float,
    created_at: datetime,
) -> PipelineComparisonRecord:
    if (
        baseline.dataset_snapshot_id != candidate.dataset_snapshot_id
        or baseline.dataset_sha256 != candidate.dataset_sha256
    ):
        raise InvalidEvaluationState("pipeline comparison requires the same frozen dataset")
    baseline_cases = {item.case_id: item for item in baseline.cases}
    candidate_cases = {item.case_id: item for item in candidate.cases}
    if set(baseline_cases) != set(candidate_cases):
        raise InvalidEvaluationState("pipeline comparison requires identical case membership")
    comparisons: list[EvaluationCaseComparison] = []
    for case_id in sorted(baseline_cases, key=str):
        baseline_score = baseline_cases[case_id].metrics.composite_score
        candidate_score = candidate_cases[case_id].metrics.composite_score
        delta = candidate_score - baseline_score
        if delta < -minimum_delta:
            outcome = ComparisonOutcome.REGRESSION
        elif delta > minimum_delta:
            outcome = ComparisonOutcome.IMPROVEMENT
        else:
            outcome = ComparisonOutcome.UNCHANGED
        comparisons.append(
            EvaluationCaseComparison(
                case_id=case_id,
                baseline_score=baseline_score,
                candidate_score=candidate_score,
                delta=delta,
                outcome=outcome,
            )
        )
    return PipelineComparisonRecord(
        comparison_id=comparison_id,
        dataset_snapshot_id=baseline.dataset_snapshot_id,
        dataset_name=baseline.dataset_name,
        dataset_version=baseline.dataset_version,
        dataset_sha256=baseline.dataset_sha256,
        baseline_metric_run_id=baseline.metric_run_id,
        baseline_pipeline=baseline.pipeline,
        candidate_metric_run_id=candidate.metric_run_id,
        candidate_pipeline=candidate.pipeline,
        minimum_delta=minimum_delta,
        overall_delta=candidate.overall.composite_score - baseline.overall.composite_score,
        cases=tuple(comparisons),
        regressions=tuple(
            item.case_id for item in comparisons if item.outcome is ComparisonOutcome.REGRESSION
        ),
        improvements=tuple(
            item.case_id for item in comparisons if item.outcome is ComparisonOutcome.IMPROVEMENT
        ),
        created_at=created_at,
    )


def _score_scope(
    pairs: Sequence[Pair],
    configuration: EvaluationMetricConfiguration,
) -> tuple[EvaluationMetricSummary, tuple[ConfidenceOutcome, ...]]:
    references = [pair[0].reference for pair in pairs]
    predictions = [pair[1].prediction for pair in pairs]
    exact_reference_fields = {
        (
            reference.case_id,
            item.field_path,
            _canonical_json(item.value_json, normalize_strings=False),
        )
        for reference, _ in pairs
        for item in reference.reference.fields
    }
    exact_predicted_fields = {
        (
            reference.case_id,
            item.field_path,
            _canonical_json(item.value_json, normalize_strings=False),
        )
        for reference, prediction in pairs
        for item in prediction.prediction.fields
    }
    normalized_reference_fields = {
        (
            reference.case_id,
            item.field_path,
            _canonical_json(
                item.value_json,
                normalize_strings=True,
                casefold=configuration.normalized_string_casefold,
            ),
        )
        for reference, _ in pairs
        for item in reference.reference.fields
    }
    normalized_predicted_fields = {
        (
            reference.case_id,
            item.field_path,
            _canonical_json(
                item.value_json,
                normalize_strings=True,
                casefold=configuration.normalized_string_casefold,
            ),
        )
        for reference, prediction in pairs
        for item in prediction.prediction.fields
    }
    fields = FieldMetricSummary(
        exact=_match_summary(exact_reference_fields, exact_predicted_fields),
        normalized=_match_summary(normalized_reference_fields, normalized_predicted_fields),
    )
    geometry = _geometry_metrics(pairs, configuration)
    reference_relationships = {
        (reference.case_id, item.subject_key, item.relation_type, item.object_key)
        for reference, _ in pairs
        for item in reference.reference.relationships
    }
    predicted_relationships = {
        (reference.case_id, item.subject_key, item.relation_type, item.object_key)
        for reference, prediction in pairs
        for item in prediction.prediction.relationships
    }
    relationships = _match_summary(reference_relationships, predicted_relationships)
    citations = _citation_metrics(pairs)
    provenance = _provenance_metrics(predictions)
    numerical = _numerical_metrics(pairs, configuration)
    ranking = _ranking_metrics(pairs, configuration.ranking_k)
    components: list[float] = []
    if any(document.fields for document in references) or any(
        document.fields for document in predictions
    ):
        components.append(fields.normalized.f1)
    if any(document.regions for document in references) or any(
        document.regions for document in predictions
    ):
        components.append(geometry.match.f1)
    if any(document.relationships for document in references) or any(
        document.relationships for document in predictions
    ):
        components.append(relationships.f1)
    if citations.expected or citations.predicted:
        components.append(citations.f1)
    if provenance.predicted_observations:
        components.append(provenance.completeness)
    if any(document.numerical_values for document in references) or any(
        document.numerical_values for document in predictions
    ):
        components.append(numerical.within_tolerance)
    if any(document.ranking_judgments for document in references):
        components.append(ranking.ndcg_at_k)
    outcomes = tuple(
        outcome
        for reference, prediction in pairs
        for outcome in _confidence_outcomes(reference, prediction, configuration)
    )
    return (
        EvaluationMetricSummary(
            fields=fields,
            geometry=geometry,
            relationships=relationships,
            citations=citations,
            provenance=provenance,
            numerical=numerical,
            ranking=ranking,
            composite_score=sum(components) / len(components) if components else 1.0,
        ),
        outcomes,
    )


def _geometry_metrics(
    pairs: Sequence[Pair],
    configuration: EvaluationMetricConfiguration,
) -> GeometryMetricSummary:
    reference = {
        (case.case_id, item.region_key): item
        for case, _ in pairs
        for item in case.reference.regions
    }
    predicted = {
        (case.case_id, item.region_key): item
        for case, prediction in pairs
        for item in prediction.prediction.regions
    }
    ious = {
        key: _compatible_iou(reference[key], predicted[key])
        for key in set(reference) & set(predicted)
    }
    true_positive = sum(
        _regions_compatible(reference[key], predicted[key])
        and value >= configuration.bounding_box_iou_threshold
        for key, value in ious.items()
    )
    return GeometryMetricSummary(
        match=_match_from_counts(
            true_positive,
            len(predicted) - true_positive,
            len(reference) - true_positive,
        ),
        comparable_regions=len(ious),
        mean_iou=sum(ious.values()) / len(ious) if ious else 0.0,
    )


def _citation_metrics(
    pairs: Sequence[Pair],
) -> CitationMetricSummary:
    expected = _evidence_by_key((reference.case_id, reference.reference) for reference, _ in pairs)
    actual = _evidence_by_key(
        (reference.case_id, prediction.prediction) for reference, prediction in pairs
    )
    expected_count = sum(len(items) for items in expected.values())
    predicted_count = sum(len(items) for items in actual.values())
    correct = sum(len(expected.get(key, set()) & evidence) for key, evidence in actual.items())
    correctness = _ratio(correct, predicted_count, empty=1.0 if expected_count == 0 else 0.0)
    completeness = _ratio(correct, expected_count, empty=1.0 if predicted_count == 0 else 0.0)
    return CitationMetricSummary(
        correct=correct,
        predicted=predicted_count,
        expected=expected_count,
        correctness=correctness,
        completeness=completeness,
        f1=_harmonic(correctness, completeness),
    )


def _provenance_metrics(
    predictions: Sequence[PredictedObservationDocument],
) -> ProvenanceMetricSummary:
    observations = [
        item
        for document in predictions
        for collection in (
            document.fields,
            document.regions,
            document.relationships,
            document.numerical_values,
        )
        for item in collection
    ]
    with_evidence = sum(bool(item.evidence_artifact_ids) for item in observations)
    return ProvenanceMetricSummary(
        predicted_observations=len(observations),
        observations_with_evidence=with_evidence,
        completeness=_ratio(with_evidence, len(observations), empty=1.0),
    )


def _numerical_metrics(
    pairs: Sequence[Pair],
    configuration: EvaluationMetricConfiguration,
) -> NumericalMetricSummary:
    expected = {
        (reference.case_id, item.field_path): item
        for reference, _ in pairs
        for item in reference.reference.numerical_values
    }
    actual = {
        (reference.case_id, item.field_path): item
        for reference, prediction in pairs
        for item in prediction.prediction.numerical_values
    }
    errors: list[float] = []
    relative_errors: list[float] = []
    for key in set(expected) & set(actual):
        reference = expected[key]
        prediction = actual[key]
        if reference.unit != prediction.unit:
            continue
        error = abs(prediction.value - reference.value)
        errors.append(error)
        if reference.value == 0:
            relative_errors.append(0.0 if error == 0 else 1.0)
        else:
            relative_errors.append(error / abs(reference.value))
    return NumericalMetricSummary(
        comparable_values=len(errors),
        mean_absolute_error=sum(errors) / len(errors) if errors else 0.0,
        root_mean_squared_error=(
            math.sqrt(sum(value * value for value in errors) / len(errors)) if errors else 0.0
        ),
        mean_relative_error=(
            sum(relative_errors) / len(relative_errors) if relative_errors else 0.0
        ),
        within_tolerance=(
            sum(value <= configuration.numerical_absolute_tolerance for value in errors)
            / len(errors)
            if errors
            else 0.0
        ),
    )


def _ranking_metrics(
    pairs: Sequence[Pair],
    ranking_k: int,
) -> RankingMetricSummary:
    judgments: dict[tuple[UUID, str], dict[str, int]] = defaultdict(dict)
    results: dict[tuple[UUID, str], list[tuple[int, str]]] = defaultdict(list)
    for reference, prediction in pairs:
        for judgment in reference.reference.ranking_judgments:
            judgments[(reference.case_id, judgment.query_id)][judgment.item_id] = judgment.relevance
        for ranked_result in prediction.prediction.ranked_results:
            results[(reference.case_id, ranked_result.query_id)].append(
                (ranked_result.rank, ranked_result.item_id)
            )
    recalls: list[float] = []
    reciprocal_ranks: list[float] = []
    ndcgs: list[float] = []
    for query_key, query_judgments in judgments.items():
        relevant = {item_id for item_id, relevance in query_judgments.items() if relevance > 0}
        if not relevant:
            continue
        ranked = [item_id for _, item_id in sorted(results.get(query_key, ()))[:ranking_k]]
        recalls.append(len(relevant & set(ranked)) / len(relevant))
        first_relevant = next(
            (position for position, item_id in enumerate(ranked, 1) if item_id in relevant),
            None,
        )
        reciprocal_ranks.append(1 / first_relevant if first_relevant is not None else 0.0)
        dcg = sum(
            (2 ** query_judgments.get(item_id, 0) - 1) / math.log2(position + 1)
            for position, item_id in enumerate(ranked, 1)
        )
        ideal = sorted(query_judgments.values(), reverse=True)[:ranking_k]
        idcg = sum(
            (2**relevance - 1) / math.log2(position + 1)
            for position, relevance in enumerate(ideal, 1)
        )
        ndcgs.append(dcg / idcg if idcg else 0.0)
    query_count = len(recalls)
    return RankingMetricSummary(
        queries=query_count,
        recall_at_k=sum(recalls) / query_count if query_count else 0.0,
        mean_reciprocal_rank=(sum(reciprocal_ranks) / query_count if query_count else 0.0),
        ndcg_at_k=sum(ndcgs) / query_count if query_count else 0.0,
    )


def _confidence_outcomes(
    reference_case: EvaluationReferenceCase,
    prediction_case: PipelineEvaluationCase,
    configuration: EvaluationMetricConfiguration,
) -> tuple[ConfidenceOutcome, ...]:
    reference = reference_case.reference
    predicted = prediction_case.prediction
    outcomes: list[ConfidenceOutcome] = []
    reference_fields = {item.field_path: item for item in reference.fields}
    for field in predicted.fields:
        if field.confidence is None:
            continue
        expected_field = reference_fields.get(field.field_path)
        correct = expected_field is not None and _canonical_json(
            field.value_json,
            normalize_strings=True,
            casefold=configuration.normalized_string_casefold,
        ) == _canonical_json(
            expected_field.value_json,
            normalize_strings=True,
            casefold=configuration.normalized_string_casefold,
        )
        outcomes.append(
            _outcome(
                reference_case.case_id,
                CalibrationCategory.FIELD,
                field.field_path or "/",
                field.confidence,
                correct,
            )
        )
    reference_regions = {item.region_key: item for item in reference.regions}
    for region in predicted.regions:
        if region.confidence is None:
            continue
        expected_region = reference_regions.get(region.region_key)
        correct = (
            expected_region is not None
            and _regions_compatible(expected_region, region)
            and _compatible_iou(expected_region, region) >= configuration.bounding_box_iou_threshold
        )
        outcomes.append(
            _outcome(
                reference_case.case_id,
                CalibrationCategory.GEOMETRY,
                region.region_key,
                region.confidence,
                correct,
            )
        )
    reference_relationships = {
        (item.subject_key, item.relation_type, item.object_key) for item in reference.relationships
    }
    for relationship in predicted.relationships:
        if relationship.confidence is None:
            continue
        relationship_key = (
            relationship.subject_key,
            relationship.relation_type,
            relationship.object_key,
        )
        outcomes.append(
            _outcome(
                reference_case.case_id,
                CalibrationCategory.RELATIONSHIP,
                "|".join(relationship_key),
                relationship.confidence,
                relationship_key in reference_relationships,
            )
        )
    reference_numerical = {item.field_path: item for item in reference.numerical_values}
    for numerical in predicted.numerical_values:
        if numerical.confidence is None:
            continue
        expected_numerical = reference_numerical.get(numerical.field_path)
        correct = (
            expected_numerical is not None
            and expected_numerical.unit == numerical.unit
            and abs(expected_numerical.value - numerical.value)
            <= configuration.numerical_absolute_tolerance
        )
        outcomes.append(
            _outcome(
                reference_case.case_id,
                CalibrationCategory.NUMERICAL,
                numerical.field_path or "/",
                numerical.confidence,
                correct,
            )
        )
    judgments = {
        (item.query_id, item.item_id): item.relevance for item in reference.ranking_judgments
    }
    for ranked_result in predicted.ranked_results:
        if ranked_result.confidence is None:
            continue
        ranking_key = (ranked_result.query_id, ranked_result.item_id)
        outcomes.append(
            _outcome(
                reference_case.case_id,
                CalibrationCategory.RANKING,
                "|".join(ranking_key),
                ranked_result.confidence,
                judgments.get(ranking_key, 0) > 0,
            )
        )
    return tuple(outcomes)


def _calibration(
    outcomes: Sequence[ConfidenceOutcome],
    configuration: EvaluationMetricConfiguration,
) -> CalibrationSummary:
    count = len(outcomes)
    buckets: list[CalibrationBucket] = []
    calibration_error = 0.0
    for index in range(configuration.calibration_bins):
        lower = index / configuration.calibration_bins
        upper = (index + 1) / configuration.calibration_bins
        selected = [
            item
            for item in outcomes
            if item.confidence >= lower
            and (item.confidence < upper or index == configuration.calibration_bins - 1)
        ]
        average_confidence = (
            sum(item.confidence for item in selected) / len(selected) if selected else 0.0
        )
        accuracy = sum(item.correct for item in selected) / len(selected) if selected else 0.0
        if count:
            calibration_error += len(selected) / count * abs(average_confidence - accuracy)
        buckets.append(
            CalibrationBucket(
                lower_bound=lower,
                upper_bound=upper,
                count=len(selected),
                average_confidence=average_confidence,
                accuracy=accuracy,
            )
        )
    coverage = []
    for threshold in configuration.confidence_thresholds:
        retained = [item for item in outcomes if item.confidence >= threshold]
        precision = sum(item.correct for item in retained) / len(retained) if retained else 0.0
        coverage.append(
            CoveragePoint(
                threshold=threshold,
                retained=len(retained),
                coverage=len(retained) / count if count else 0.0,
                precision=precision,
                risk=1 - precision if retained else 0.0,
            )
        )
    return CalibrationSummary(
        observations=count,
        brier_score=(
            sum((item.confidence - float(item.correct)) ** 2 for item in outcomes) / count
            if count
            else 0.0
        ),
        expected_calibration_error=calibration_error,
        buckets=tuple(buckets),
        coverage_curve=tuple(coverage),
    )


def _group_results(
    pairs: Sequence[Pair],
    scoring_input: EvaluationScoringInput,
    configuration: EvaluationMetricConfiguration,
) -> tuple[EvaluationMetricGroupResult, ...]:
    grouped: dict[tuple[EvaluationGroupDimension, str], list[Pair]] = defaultdict(list)
    for dimension in configuration.group_by:
        if dimension is EvaluationGroupDimension.PIPELINE_VERSION:
            grouped[(dimension, scoring_input.submission.pipeline.version)].extend(pairs)
            continue
        if dimension is EvaluationGroupDimension.MODEL_VERSION:
            for model in scoring_input.submission.model_versions:
                grouped[(dimension, f"{model.provider}/{model.name}@{model.version}")].extend(pairs)
            continue
        for pair in pairs:
            value = _dimension_value(pair[0].dimensions, dimension)
            if value is not None:
                grouped[(dimension, value)].append(pair)
    results = []
    for (dimension, value), group_pairs in sorted(
        grouped.items(), key=lambda item: (item[0][0].value, item[0][1])
    ):
        metrics, outcomes = _score_scope(tuple(group_pairs), configuration)
        results.append(
            EvaluationMetricGroupResult(
                dimension=dimension,
                value=value,
                case_count=len(group_pairs),
                metrics=metrics,
                calibration=_calibration(outcomes, configuration),
            )
        )
    return tuple(results)


def _dimension_value(
    dimensions: EvaluationCaseDimensions,
    dimension: EvaluationGroupDimension,
) -> str | None:
    if dimension is EvaluationGroupDimension.REVIEW_STATUS:
        return dimensions.review_status.value
    value = getattr(dimensions, dimension.value, None)
    return str(value) if value is not None else None


def _match_summary[T](reference: set[T], predicted: set[T]) -> MatchMetricSummary:
    true_positive = len(reference & predicted)
    return _match_from_counts(
        true_positive,
        len(predicted) - true_positive,
        len(reference) - true_positive,
    )


def _match_from_counts(
    true_positive: int,
    false_positive: int,
    false_negative: int,
) -> MatchMetricSummary:
    predicted = true_positive + false_positive
    expected = true_positive + false_negative
    precision = _ratio(true_positive, predicted, empty=1.0 if expected == 0 else 0.0)
    recall = _ratio(true_positive, expected, empty=1.0 if predicted == 0 else 0.0)
    return MatchMetricSummary(
        true_positive=true_positive,
        false_positive=false_positive,
        false_negative=false_negative,
        precision=precision,
        recall=recall,
        f1=_harmonic(precision, recall),
    )


def _regions_compatible(
    reference: ReferenceRegionObservation,
    predicted: PredictedRegionObservation,
) -> bool:
    return (
        reference.annotation_type == predicted.annotation_type
        and reference.region.source_artifact_id == predicted.region.source_artifact_id
        and reference.region.canvas_width == predicted.region.canvas_width
        and reference.region.canvas_height == predicted.region.canvas_height
        and reference.region.page_number == predicted.region.page_number
    )


def _compatible_iou(
    reference: ReferenceRegionObservation,
    predicted: PredictedRegionObservation,
) -> float:
    if not _regions_compatible(reference, predicted):
        return 0.0
    first = reference.region
    second = predicted.region
    left = max(first.x, second.x)
    top = max(first.y, second.y)
    right = min(first.x + first.width, second.x + second.width)
    bottom = min(first.y + first.height, second.y + second.height)
    intersection = max(0.0, right - left) * max(0.0, bottom - top)
    union = first.width * first.height + second.width * second.height - intersection
    return intersection / union if union else 0.0


def _evidence_by_key(
    documents: Iterable[tuple[UUID, ReferenceObservationDocument | PredictedObservationDocument]],
) -> dict[str, set[UUID]]:
    result: dict[str, set[UUID]] = {}
    for case_id, document in documents:
        for field in document.fields:
            result[f"{case_id}:field:{field.field_path}"] = set(field.evidence_artifact_ids)
        for region in document.regions:
            result[f"{case_id}:region:{region.region_key}"] = set(region.evidence_artifact_ids)
        for relationship in document.relationships:
            result[
                f"{case_id}:relationship:{relationship.subject_key}|"
                f"{relationship.relation_type}|{relationship.object_key}"
            ] = set(relationship.evidence_artifact_ids)
        for numerical in document.numerical_values:
            result[f"{case_id}:numerical:{numerical.field_path}"] = set(
                numerical.evidence_artifact_ids
            )
    return result


def _canonical_json(
    value: str,
    *,
    normalize_strings: bool,
    casefold: bool = False,
) -> str:
    parsed = json.loads(value)
    if normalize_strings:
        parsed = _normalize_json_value(parsed, casefold=casefold)
    return json.dumps(parsed, sort_keys=True, separators=(",", ":"), ensure_ascii=False)


def _normalize_json_value(value: object, *, casefold: bool) -> object:
    if isinstance(value, str):
        normalized = " ".join(value.split())
        return normalized.casefold() if casefold else normalized
    if isinstance(value, list):
        return [_normalize_json_value(item, casefold=casefold) for item in value]
    if isinstance(value, dict):
        return {key: _normalize_json_value(item, casefold=casefold) for key, item in value.items()}
    return value


def evaluation_scoring_input_sha256(scoring_input: EvaluationScoringInput) -> str:
    encoded = json.dumps(
        scoring_input.model_dump(mode="json"),
        sort_keys=True,
        separators=(",", ":"),
    ).encode()
    return hashlib.sha256(encoded).hexdigest()


def _outcome(
    case_id: UUID,
    category: CalibrationCategory,
    observation_key: str,
    confidence: float,
    correct: bool,
) -> ConfidenceOutcome:
    return ConfidenceOutcome(
        case_id=case_id,
        category=category,
        observation_key=observation_key,
        confidence=confidence,
        correct=correct,
    )


def _ratio(numerator: int, denominator: int, *, empty: float) -> float:
    return numerator / denominator if denominator else empty


def _harmonic(first: float, second: float) -> float:
    return 2 * first * second / (first + second) if first + second else 0.0
