# ADR 0010: Freeze reviewed cases into content-addressed golden snapshots

## Status

Accepted.

## Decision

Manage golden datasets as mutable, optimistically versioned drafts that can be frozen exactly once
into immutable snapshots. A draft member follows an explicit state machine from `unlabeled` through
prediction, review, optional adjudication, gold candidacy, and gold. Every transition is append-only
and records its actor, rationale, selected annotation revision, and timestamp. Retired cases remain
in draft history but are excluded from the next snapshot.

Require every included case to be gold and pin its exact annotation revision, adjudication record
when applicable, source-artifact metadata and hashes, prediction IDs, review status, split, and
promotion provenance. Derive the snapshot SHA-256 from canonical ordered content and derive the
snapshot UUID from that hash. Store the complete canonical snapshot as JSONB while retaining
relational rows for dataset membership, transitions, paper splits, and source-content hashes.

Enforce leakage rules at both contract and persistence boundaries. All cases from one stable paper
key must use one split, and identical artifact content must not cross splits even when artifact UUIDs
or paper keys differ. Organization-private artifacts cannot enter this shared golden-dataset path
until PRD-017 supplies an authorization-aware private-dataset policy.

Publish deterministic JSONL cases and a JSON manifest as immutable, content-addressed S3 objects.
The manifest includes snapshot identity, split counts, artifact hashes, exact schema-versioned case
documents, and the generated predecessor changelog. A successor names an immutable predecessor
snapshot and must use a new dataset version. Deleting its mutable draft does not delete or invalidate
any frozen snapshot or export.

## Consequences

- Correcting a gold label requires a successor dataset version and produces an inspectable changelog.
- Repeating freeze or export publication is idempotent and cannot silently replace frozen content.
- Exports are independently validated by strict Pydantic contracts and committed JSON Schemas.
- Snapshots intentionally duplicate reviewed scientific state so later edits cannot change a frozen
  reference.
- Parquet summaries and automatic predecessor-draft cloning remain optional follow-up work.
