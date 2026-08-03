"""Deterministic retrieval metrics and activation-gate decisions."""

from __future__ import annotations

import math
from collections.abc import Iterable, Mapping, Sequence
from uuid import UUID

from hiveblot_contracts import (
    ArtifactVisibility,
    EvidenceIndexDocument,
    EvidenceSearchFilters,
    EvidenceSearchHit,
    EvidenceSearchResult,
    RetrievalAggregateMetrics,
    RetrievalEvaluationConfiguration,
    RetrievalEvaluationDatasetRecord,
    RetrievalEvaluationQuery,
    RetrievalMetricAtK,
    RetrievalQueryMetric,
)


def score_retrieval_results(
    dataset: RetrievalEvaluationDatasetRecord,
    configuration: RetrievalEvaluationConfiguration,
    results: Mapping[str, EvidenceSearchResult],
) -> tuple[RetrievalAggregateMetrics, tuple[RetrievalQueryMetric, ...], bool]:
    query_metrics = tuple(
        _score_query(query, configuration, results[query.query_id]) for query in dataset.queries
    )
    metrics = RetrievalAggregateMetrics(
        recall_at_k=tuple(
            RetrievalMetricAtK(
                k=k,
                value=_mean(_metric_at_k(item.recall_at_k, k) for item in query_metrics),
            )
            for k in configuration.k_values
        ),
        mean_reciprocal_rank=_mean(item.reciprocal_rank for item in query_metrics),
        ndcg_at_k=tuple(
            RetrievalMetricAtK(
                k=k,
                value=_mean(_metric_at_k(item.ndcg_at_k, k) for item in query_metrics),
            )
            for k in configuration.k_values
        ),
        filter_correctness=_mean(item.filter_correctness for item in query_metrics),
        citation_correctness=_mean(item.citation_correctness for item in query_metrics),
        cross_tenant_leakage_rate=_ratio(
            sum(item.cross_tenant_leaks for item in query_metrics),
            sum(len(item.retrieved_document_ids) for item in query_metrics),
            empty=0.0,
        ),
    )
    passed = (
        metrics.recall_at_k[-1].value >= configuration.minimum_recall_at_largest_k
        and metrics.filter_correctness >= configuration.minimum_filter_correctness
        and metrics.citation_correctness >= configuration.minimum_citation_correctness
        and metrics.cross_tenant_leakage_rate <= configuration.maximum_cross_tenant_leakage_rate
    )
    return metrics, query_metrics, passed


def _score_query(
    query: RetrievalEvaluationQuery,
    configuration: RetrievalEvaluationConfiguration,
    result: EvidenceSearchResult,
) -> RetrievalQueryMetric:
    largest_k = configuration.k_values[-1]
    hits = result.hits[:largest_k]
    retrieved = tuple(item.document.document_id for item in hits)
    relevance = {item.document_id: item.relevance for item in query.relevant}
    relevant_ids = set(relevance)
    recall = tuple(
        RetrievalMetricAtK(
            k=k,
            value=_ratio(
                len(set(retrieved[:k]) & relevant_ids),
                len(relevant_ids),
                empty=1.0,
            ),
        )
        for k in configuration.k_values
    )
    first_relevant = next(
        (
            rank
            for rank, document_id in enumerate(retrieved, start=1)
            if document_id in relevant_ids
        ),
        None,
    )
    ndcg = tuple(
        RetrievalMetricAtK(
            k=k,
            value=_ndcg(retrieved[:k], relevance, k),
        )
        for k in configuration.k_values
    )
    filter_correctness = _ratio(
        sum(_document_matches_filters(item.document, query.expected_filters) for item in hits),
        len(hits),
        empty=1.0,
    )
    citation_correctness = _citation_correctness(query, hits)
    allowed = set(query.allowed_organization_ids)
    leaks = sum(
        item.document.visibility is ArtifactVisibility.ORGANIZATION_PRIVATE
        and item.document.organization_id not in allowed
        for item in hits
    )
    return RetrievalQueryMetric(
        query_id=query.query_id,
        retrieved_document_ids=retrieved,
        recall_at_k=recall,
        ndcg_at_k=ndcg,
        reciprocal_rank=0.0 if first_relevant is None else 1.0 / first_relevant,
        filter_correctness=filter_correctness,
        citation_correctness=citation_correctness,
        cross_tenant_leaks=leaks,
    )


def _citation_correctness(
    query: RetrievalEvaluationQuery,
    hits: Sequence[EvidenceSearchHit],
) -> float:
    hit_by_id = {item.document.document_id: item for item in hits}
    scores: list[float] = []
    for judgment in query.relevant:
        if not judgment.required_citation_artifact_ids:
            continue
        hit = hit_by_id.get(judgment.document_id)
        if hit is None:
            continue
        actual = {item.artifact.artifact_id for item in hit.document.citations}
        expected = set(judgment.required_citation_artifact_ids)
        scores.append(_ratio(len(actual & expected), len(expected), empty=1.0))
    has_expectations = any(item.required_citation_artifact_ids for item in query.relevant)
    return _mean(scores) if scores else (0.0 if has_expectations else 1.0)


def _document_matches_filters(
    document: EvidenceIndexDocument,
    filters: EvidenceSearchFilters,
) -> bool:
    for values, expected in (
        (document.proteins, filters.proteins),
        (document.biological_systems, filters.biological_systems),
        (document.treatments, filters.treatments),
        (document.conditions, filters.conditions),
    ):
        if expected and not {_normalize(value) for value in values} & {
            _normalize(value) for value in expected
        }:
            return False
    if filters.review_statuses and document.review_status not in filters.review_statuses:
        return False
    return not (
        filters.evidence_qualities and document.evidence_quality not in filters.evidence_qualities
    )


def _ndcg(retrieved: Sequence[UUID], relevance: Mapping[UUID, int], k: int) -> float:
    actual = _discounted_gain(relevance.get(document_id, 0) for document_id in retrieved[:k])
    ideal = _discounted_gain(sorted(relevance.values(), reverse=True)[:k])
    return _ratio(actual, ideal, empty=1.0)


def _discounted_gain(values: Iterable[int]) -> float:
    return sum(
        (2**relevance - 1) / math.log2(rank + 1) for rank, relevance in enumerate(values, start=1)
    )


def _metric_at_k(values: Sequence[RetrievalMetricAtK], k: int) -> float:
    return next(item.value for item in values if item.k == k)


def _normalize(value: str) -> str:
    return value.strip().casefold()


def _mean(values: Iterable[float]) -> float:
    materialized = tuple(values)
    return sum(materialized) / len(materialized) if materialized else 0.0


def _ratio(numerator: float, denominator: float, *, empty: float) -> float:
    return numerator / denominator if denominator else empty
