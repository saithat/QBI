# ADR 0005: Aggregate evidence metadata, stream source bytes directly

## Status

Accepted.

## Decision

The source workbench API aggregates case associations, immutable artifact metadata, caption and
nearby-text context, and spatial overlays. It does not return large bytes or pre-create access URLs.
The browser requests an expiring URL only for the selected source and loads that URL directly from
the S3-compatible object store.

Prediction, reviewer, and adjudication overlays are a strict discriminated union. Reviewer overlays
come from each annotation document's immutable head revision; adjudication overlays come from the
selected immutable revision. They remain distinct even when they describe the same geometry.
Prediction evidence may now carry an optional source-pixel `BoundingRegion`; older evidence records
without geometry remain valid and simply do not produce a drawable overlay.

## Consequences

- Source metadata and hashes are inspectable without reading object bytes.
- Access remains expiring and auditable through the PRD-002 artifact service.
- Only the active source is requested, allowing the browser/object store to stream and progressively
  decode large images without routing them through FastAPI.
- Context revisions are append-only, link to their prior revision, and use optimistic head checks;
  their composite foreign key targets the exact case/artifact/role association.
- Spatial mutation remains deferred to PRD-007; this workbench is read-only.
