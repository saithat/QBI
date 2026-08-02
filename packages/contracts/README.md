# HiveBlot contracts

This package is the canonical process-boundary contract layer. Models are Pydantic v2,
strict, immutable, versioned, and reject unknown fields. It contains no database, web,
worker, or model-client imports.

Generate snapshots with `uv run hiveblot-schemas`. Verify committed snapshots with
`uv run hiveblot-schemas --check`.
