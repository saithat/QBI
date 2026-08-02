# HiveBlot contracts

This package is the canonical process-boundary contract layer. Models are Pydantic v2,
strict, immutable, versioned, and reject unknown fields. It contains no database, web,
worker, or model-client imports.

PRD-006 adds the western-blot-specific structured annotation and semantic comparison schemas. This
is intentionally not a generic multi-assay payload; future modalities should add explicit adapters
after the western-blot evaluation surface is mature.

PRD-007 adds `SpatialAnnotationSet` and `SpatialAnnotationComparison`. The graph vocabulary is
western-blot-review focused, stores geometry in immutable source pixels, validates relationship
semantics, and remains distinct from API and persistence representations.

PRD-008 adds immutable pipeline DAG, run, invocation/result, evidence, replay, and publication
contracts. A terminal attempt retains raw and normalized representations separately; publication
values name their producing invocation and JSON Pointer.

PRD-011 adds stored PDF/image western-blot extraction inputs, figure candidates, strict model
prediction grids, review-ready structured/spatial results, run records, and replay records. The
raw provider response remains outside the normalized contract and is preserved by pipeline and
evaluation records before normalization.

Generate snapshots with `uv run hiveblot-schemas`. Verify committed snapshots with
`uv run hiveblot-schemas --check`.
