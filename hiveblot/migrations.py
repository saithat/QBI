"""Minimal ordered PostgreSQL migration runner for application-owned structured state."""

from __future__ import annotations

from pathlib import Path

import psycopg

MIGRATIONS = Path(__file__).with_name("migrations")
MIGRATION_LOCK_ID = 4_454_566_677_608_404_068


def apply_migrations(database_url: str) -> None:
    """Apply committed `*.up.sql` files once, in lexical order."""

    with psycopg.connect(database_url) as connection:
        connection.execute(
            """
            CREATE TABLE IF NOT EXISTS schema_migrations (
                version TEXT PRIMARY KEY,
                applied_at TIMESTAMPTZ NOT NULL DEFAULT now()
            )
            """
        )
        connection.execute("SELECT pg_advisory_xact_lock(%s)", (MIGRATION_LOCK_ID,))
        applied = {
            row[0] for row in connection.execute("SELECT version FROM schema_migrations").fetchall()
        }
        for path in sorted(MIGRATIONS.glob("*.up.sql")):
            version = path.name.removesuffix(".up.sql")
            if version in applied:
                continue
            connection.execute(path.read_text(encoding="utf-8"), prepare=False)
            connection.execute(
                "INSERT INTO schema_migrations (version) VALUES (%s)",
                (version,),
            )
