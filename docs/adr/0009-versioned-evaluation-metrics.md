# ADR 0009: Score immutable dataset snapshots into immutable metric runs

## Status

Accepted.

## Decision

Evaluate one explicitly versioned pipeline submission against one content-addressed, frozen
reference snapshot. Both sides must contain exactly the same stable evaluation case IDs. Keep the
reference observations, predicted observations, scoring configuration, scorer identity, pipeline
identity, model versions, and resulting metrics in strict Pydantic contracts.

Compute deterministic case, group, and overall summaries for exact and normalized fields,
geometry, relationships, citations, provenance, numerical observations, and rankings. Compute
confidence outcomes independently for fields, geometry, relationships, numerical values, and
ranked results, then derive reliability buckets, Brier score, expected calibration error, and
precision/risk/coverage threshold curves. A metric run is append-only and never modifies a
prediction, annotation, adjudication, or dataset snapshot.

Persist the complete canonical result as JSONB and duplicate only stable lookup keys and case
scores into constrained relational columns. Compare pipeline runs only when their snapshot UUID,
content hash, and case membership match. Comparisons are derived records: they identify
case-level improvements and regressions without changing either run.

Expose explicit HTTP adapters, a reproducible CLI, and an inspect-only browser dashboard. The CLI
derives its run UUID from the canonical input hash and uses the dataset freeze time so identical
inputs produce byte-identical output. Service/API-created runs receive fresh identities and retain
their creation time.

## Consequences

- Pipeline versions can be compared on identical scientific references without mutating review
  state.
- Field and geometry scores remain separate, while a documented composite provides case ordering.
- Missing evidence and confidence become measurable product behavior rather than dashboard-only
  metadata.
- Empty or inapplicable metric categories retain explicit neutral/zero conventions documented with
  the scorer version.
- Dataset lifecycle, promotion, leakage checks, and immutable export construction remain PRD-010;
  PRD-009 accepts an already frozen, content-addressed reference contract.
