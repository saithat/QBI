# ADR 0003: Append-only scientific review records

- Status: accepted
- Date: 2026-08-02
- PRD: 003

## Decision

Predictions, reviewer documents, reviewer revisions, and adjudications are separate records.
Predictions are immutable. Each `(evaluation case, reviewer)` pair owns one annotation document,
whose only mutable scientific pointer is `head_revision_id`. Every save inserts a complete new
snapshot and atomically advances that pointer when the caller's expected head still matches.

Field annotations target either a versioned JSON Pointer or a stable entity UUID through a
discriminated union. Spatial annotations store immutable source-pixel coordinates. Adjudications
select and cite reviewed revisions; they do not copy over or rewrite those revisions.

## Consequences

- Historic review state is queryable without reconstructing database audit logs.
- A stale editor receives a concurrency conflict and cannot silently overwrite another edit.
- Multiple reviewers can disagree without sharing a mutable annotation record.
- Review states explicitly distinguish present, absent, unknown, ambiguous, and not applicable.
- Revision tables have no update API. Correcting a gold label later requires a new revision and,
  once PRD-010 exists, a new dataset version.
- Authentication is deferred to PRD-017; reviewer UUIDs are explicit but not yet identity claims.
