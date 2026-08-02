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
    band_state, confidence
) VALUES (
    %(paper_id)s, %(candidate_key)s, %(source_pdf)s, %(candidate_path)s, %(page)s,
    %(figure_label)s, %(panel_label)s, %(row_index)s, %(lane_index)s, %(target)s,
    %(is_loading_control)s, %(western_blot_type)s, %(sample)s, %(organism)s,
    %(treatment_context)s, %(condition)s, %(band_state)s, %(confidence)s
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
    updated_at = now()
"""

RECORD_SELECT = """
SELECT id, paper_id, source_pdf, candidate_path, page, figure_label,
       panel_label, row_index, lane_index, target, is_loading_control,
       western_blot_type, sample, organism, treatment_context, condition,
       band_state, confidence, updated_at
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


def upsert_records(database_url: str, records: Sequence[WesternBlotRecordWrite]) -> int:
    if not records:
        return 0
    with psycopg.connect(database_url) as connection, connection.cursor() as cursor:
        cursor.executemany(UPSERT_SQL, records)
    return len(records)


def build_record_query(
    filters: RecordSearchCriteria,
    *,
    broad_query: str | None = None,
    limit: int = 100,
    offset: int = 0,
) -> tuple[str, list[Any]]:
    clauses: list[str] = []
    params: list[Any] = []

    if filters.target:
        clauses.append("target ILIKE %s")
        params.append(f"%{filters.target}%")
    if filters.sample:
        for value in _search_terms(filters.sample, SAMPLE_SEARCH_STOPWORDS):
            clauses.append("(sample ILIKE %s OR organism ILIKE %s)")
            term = f"%{value}%"
            params.extend((term, term))
    if filters.condition:
        for value in _search_terms(filters.condition, CONDITION_SEARCH_STOPWORDS):
            clauses.append("(condition ILIKE %s OR treatment_context ILIKE %s)")
            term = f"%{value}%"
            params.extend((term, term))
    if not clauses and broad_query:
        clauses.append(
            "(target ILIKE %s OR sample ILIKE %s OR organism ILIKE %s "
            "OR condition ILIKE %s OR treatment_context ILIKE %s)"
        )
        term = f"%{broad_query.strip()}%"
        params.extend([term] * 5)

    where = f" WHERE {' AND '.join(clauses)}" if clauses else ""
    query = (
        "SELECT id, paper_id, page, figure_label, panel_label, target, "
        "is_loading_control, western_blot_type, sample, organism, "
        "treatment_context, condition, band_state, confidence, updated_at "
        f"FROM western_blot_records{where} "
        "ORDER BY updated_at DESC, id DESC LIMIT %s OFFSET %s"
    )
    params.extend((max(1, min(limit, 200)), max(0, offset)))
    return query, params


def _search_terms(value: str, stopwords: set[str]) -> list[str]:
    terms = re.findall(r"[A-Za-z0-9][A-Za-z0-9_.+-]*", value)
    meaningful = [term for term in terms if term.casefold() not in stopwords]
    return meaningful or [value.strip()]


def list_records(
    database_url: str,
    filters: RecordSearchCriteria,
    *,
    broad_query: str | None = None,
    limit: int = 100,
    offset: int = 0,
) -> list[WesternBlotRecordListRow]:
    query, params = build_record_query(
        filters,
        broad_query=broad_query,
        limit=limit,
        offset=offset,
    )
    with psycopg.connect(database_url, row_factory=dict_row) as connection:
        rows = connection.execute(query, params).fetchall()
        return cast(list[WesternBlotRecordListRow], list(rows))


def get_record(database_url: str, record_id: int) -> WesternBlotRecordDetailRow | None:
    with psycopg.connect(database_url, row_factory=dict_row) as connection:
        row = connection.execute(
            f"{RECORD_SELECT} WHERE id = %s",
            (record_id,),
        ).fetchone()
        return cast(WesternBlotRecordDetailRow | None, row)
