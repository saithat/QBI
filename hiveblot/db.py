from __future__ import annotations

import re
from collections.abc import Sequence
from pathlib import Path
from typing import Any, cast

import psycopg
from psycopg.rows import dict_row

from .domain import RecordSearchCriteria
from .persistence_models import (
    WesternBlotRecordDetailRow,
    WesternBlotRecordListRow,
    WesternBlotRecordWrite,
)

SAMPLE_SEARCH_STOPWORDS = {
    "cell",
    "cells",
    "line",
    "sample",
    "samples",
    "tissue",
}
CONDITION_SEARCH_STOPWORDS = {
    "after",
    "before",
    "condition",
    "conditions",
    "during",
    "exposed",
    "exposure",
    "treated",
    "treatment",
    "under",
    "with",
}

UPSERT_SQL = """
INSERT INTO western_blot_records (
    paper_id, candidate_key, source_pdf, candidate_path, page, figure_label,
    panel_label, row_index, lane_index, target, is_loading_control,
    western_blot_type, sample, organism, treatment_context, condition,
    band_state, confidence, image_sha256, model_version, source_url, source_id
) VALUES (
    %(paper_id)s, %(candidate_key)s, %(source_pdf)s, %(candidate_path)s, %(page)s,
    %(figure_label)s, %(panel_label)s, %(row_index)s, %(lane_index)s, %(target)s,
    %(is_loading_control)s, %(western_blot_type)s, %(sample)s, %(organism)s,
    %(treatment_context)s, %(condition)s, %(band_state)s, %(confidence)s,
    %(image_sha256)s, %(model_version)s, %(source_url)s, %(source_id)s
)
ON CONFLICT ON CONSTRAINT western_blot_records_identity_key DO UPDATE SET
    source_pdf = EXCLUDED.source_pdf,
    candidate_path = EXCLUDED.candidate_path,
    figure_label = EXCLUDED.figure_label,
    is_loading_control = EXCLUDED.is_loading_control,
    western_blot_type = EXCLUDED.western_blot_type,
    sample = EXCLUDED.sample,
    organism = EXCLUDED.organism,
    treatment_context = EXCLUDED.treatment_context,
    condition = EXCLUDED.condition,
    band_state = EXCLUDED.band_state,
    confidence = EXCLUDED.confidence,
    image_sha256 = EXCLUDED.image_sha256,
    model_version = EXCLUDED.model_version,
    source_url = EXCLUDED.source_url,
    source_id = EXCLUDED.source_id,
    updated_at = now()
WHERE western_blot_records.source_id IS NOT DISTINCT FROM EXCLUDED.source_id
RETURNING id
"""

RECORD_SELECT = """
SELECT id, paper_id, source_pdf, candidate_path, page, figure_label,
       panel_label, row_index, lane_index, target, is_loading_control,
       western_blot_type, sample, organism, treatment_context, condition,
       band_state, confidence, image_sha256, model_version, source_url, source_id, updated_at
FROM western_blot_records
"""


def initialize(database_url: str) -> None:
    schema = Path(__file__).with_name("schema.sql").read_text(encoding="utf-8")
    with psycopg.connect(database_url) as connection:
        connection.execute(schema)


def health(database_url: str) -> bool:
    try:
        with psycopg.connect(database_url, connect_timeout=3) as connection:
            connection.execute("SELECT 1")
        return True
    except psycopg.Error:
        return False


def _write_parameters(records: Sequence[WesternBlotRecordWrite]) -> list[dict[str, Any]]:
    return [
        {
            "image_sha256": None,
            "model_version": None,
            "source_url": None,
            "source_id": None,
            **record,
        }
        for record in records
    ]


def replace_records_for_source(
    database_url: str,
    paper_id: str,
    records: Sequence[WesternBlotRecordWrite],
    *,
    source_id: str | None = None,
) -> int:
    """Keep IDs for current bands and remove stale bands only within the specified source."""
    if not paper_id.strip() or any(record["paper_id"] != paper_id for record in records):
        raise ValueError("Replacement records must all belong to the specified paper")
    if (source_id is not None and not source_id.strip()) or any(
        record.get("source_id") != source_id for record in records
    ):
        raise ValueError("Replacement records must all belong to the specified source")
    parameters = _write_parameters(records)
    with psycopg.connect(database_url) as connection, connection.cursor() as cursor:
        cursor.execute("SELECT pg_advisory_xact_lock(hashtextextended(%s, 0))", (paper_id,))
        retained_ids = []
        for record in parameters:
            result = cursor.execute(UPSERT_SQL, record).fetchone()
            if result is None:
                raise ValueError("Record identity belongs to a different source")
            retained_ids.append(result[0])
        cursor.execute(
            "DELETE FROM western_blot_records "
            "WHERE paper_id = %s AND source_id IS NOT DISTINCT FROM %s AND id <> ALL(%s)",
            (paper_id, source_id, retained_ids),
        )
    return len(records)


def _record_where(
    filters: RecordSearchCriteria,
) -> tuple[str, list[Any]]:
    clauses: list[str] = []
    params: list[Any] = []
    if filters.paper_id and filters.paper_id.strip():
        clauses.append("paper_id = %s")
        params.append(filters.paper_id.strip())
    if filters.target and filters.target.strip():
        clauses.append("target ILIKE %s")
        params.append(f"%{filters.target.strip()}%")
    if filters.sample and filters.sample.strip():
        for value in _search_terms(filters.sample, SAMPLE_SEARCH_STOPWORDS):
            clauses.append("(sample ILIKE %s OR organism ILIKE %s)")
            term = f"%{value}%"
            params.extend((term, term))
    if filters.condition and filters.condition.strip():
        for value in _search_terms(filters.condition, CONDITION_SEARCH_STOPWORDS):
            clauses.append("(condition ILIKE %s OR treatment_context ILIKE %s)")
            term = f"%{value}%"
            params.extend((term, term))
    if filters.q and filters.q.strip():
        for value in filters.q.split():
            clauses.append(
                "(target ILIKE %s OR sample ILIKE %s OR organism ILIKE %s "
                "OR condition ILIKE %s OR treatment_context ILIKE %s OR paper_id ILIKE %s)"
            )
            params.extend([f"%{value}%"] * 6)
    return (f" WHERE {' AND '.join(clauses)}" if clauses else ""), params


def build_record_query(
    filters: RecordSearchCriteria,
    *,
    limit: int = 100,
    offset: int = 0,
) -> tuple[str, list[Any]]:
    where, params = _record_where(filters)
    query = (
        "SELECT id, paper_id, page, figure_label, panel_label, row_index, lane_index, target, "
        "is_loading_control, western_blot_type, sample, organism, treatment_context, condition, "
        "band_state, confidence, image_sha256, model_version, source_url, source_id, updated_at "
        f"FROM western_blot_records{where} "
        "ORDER BY updated_at DESC, id DESC LIMIT %s OFFSET %s"
    )
    params.extend((max(1, min(limit, 200)), max(0, offset)))
    return query, params


def _search_terms(value: str, stopwords: set[str]) -> list[str]:
    terms = re.findall(r"[A-Za-z0-9][A-Za-z0-9_.+-]*", value)
    meaningful = [term for term in terms if term.casefold() not in stopwords]
    return meaningful or [value.strip()]


def search_records(
    database_url: str,
    filters: RecordSearchCriteria,
    *,
    limit: int = 100,
    offset: int = 0,
) -> tuple[list[WesternBlotRecordListRow], int]:
    """Read the page and its full count from one database snapshot, including empty pages."""
    query, params = build_record_query(filters, limit=limit, offset=offset)
    where, count_params = _record_where(filters)
    with psycopg.connect(database_url, row_factory=dict_row) as connection:
        connection.execute("SET TRANSACTION ISOLATION LEVEL REPEATABLE READ READ ONLY")
        count_row = connection.execute(
            f"SELECT count(*) AS total FROM western_blot_records{where}", count_params
        ).fetchone()
        rows = connection.execute(query, params).fetchall()
    assert count_row is not None
    return cast(list[WesternBlotRecordListRow], rows), int(count_row["total"])


def get_record(database_url: str, record_id: int) -> WesternBlotRecordDetailRow | None:
    with psycopg.connect(database_url, row_factory=dict_row) as connection:
        row = connection.execute(f"{RECORD_SELECT} WHERE id = %s", (record_id,)).fetchone()
        return cast(WesternBlotRecordDetailRow | None, row)
