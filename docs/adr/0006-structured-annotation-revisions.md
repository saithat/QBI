# ADR 0006: Store typed western-blot snapshots on immutable review revisions

## Status

Accepted.

## Decision

Add a strict `WesternBlotStructuredAnnotation` contract for western-blot scientific metadata and
store an optional validated snapshot on each existing immutable `AnnotationRevision`. PostgreSQL
stores the snapshot as JSONB, while the mapping layer deliberately revalidates JSON-mode data into
the frozen canonical contract. Historic generic revisions remain valid with a null structured
snapshot.

The structured service is an adapter over the PRD-003 evaluation service. A settled autosave appends
a revision using the expected head UUID. Undo copies a selected historic snapshot into another new
revision; it never moves the document head backward or changes the target revision.

Structured predictions remain separate `PredictionDocument` records. Individual and bulk
acceptance return a proposed reviewer document, and semantic comparison uses stable entity IDs and
field paths. Saving that proposal is an explicit review revision.

## Consequences

- Protein targets, loading controls, biological contexts, treatments, lanes, antibodies, molecular
  weights, replicates, and their relationships share one versioned contract.
- Original extracted text, explicit ambiguity states, evidence-region IDs, reviewer notes, error
  codes, and canonical references remain inspectable.
- Old annotations and the generic field/spatial records are preserved. Structured autosaves carry
  existing generic and spatial snapshots forward.
- JSONB is a persistence representation, not a validation bypass; unknown or coercible-only data
  cannot enter the canonical revision.
- Spatial mutation remains deferred to PRD-007.
