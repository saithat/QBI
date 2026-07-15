from __future__ import annotations

from collections.abc import Sequence
from pathlib import Path
from typing import Any

import psycopg
from psycopg.rows import dict_row

from .model_client import SearchFilters

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


def upsert_records(database_url: str, records: Sequence[dict[str, Any]]) -> int:
    if not records:
        return 0
    with psycopg.connect(database_url) as connection, connection.cursor() as cursor:
        cursor.executemany(UPSERT_SQL, records)
    return len(records)


def build_record_query(
    filters: SearchFilters,
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
        clauses.append("(sample ILIKE %s OR organism ILIKE %s)")
        term = f"%{filters.sample}%"
        params.extend((term, term))
    if filters.condition:
        clauses.append("(condition ILIKE %s OR treatment_context ILIKE %s)")
        term = f"%{filters.condition}%"
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


def list_records(
    database_url: str,
    filters: SearchFilters,
    *,
    broad_query: str | None = None,
    limit: int = 100,
    offset: int = 0,
) -> list[dict[str, Any]]:
    query, params = build_record_query(
        filters,
        broad_query=broad_query,
        limit=limit,
        offset=offset,
    )
    with psycopg.connect(database_url, row_factory=dict_row) as connection:
        return list(connection.execute(query, params).fetchall())
