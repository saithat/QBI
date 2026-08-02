# HiveBlot contracts

This package is the canonical process-boundary contract layer. Models are Pydantic v2,
strict, immutable, versioned, and reject unknown fields. It contains no database, web,
worker, or model-client imports.

PRD-006 adds the western-blot-specific structured annotation and semantic comparison schemas. This
is intentionally not a generic multi-assay payload; future modalities should add explicit adapters
after the western-blot evaluation surface is mature.

Generate snapshots with `uv run hiveblot-schemas`. Verify committed snapshots with
`uv run hiveblot-schemas --check`.
