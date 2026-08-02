# ADR 0011: Migrate western-blot extraction through versioned stage contracts

## Status

Accepted.

## Decision

Retain the useful hackathon PDF, OpenCV, prompt, and local Qwen-compatible model behavior behind a
configurable `WesternBlotExtractionImplementationAdapter`. Execute it as three independently
recorded scientific components: `figure_detection`, `western_blot_vision`, and `case_assembly`.
Every implementation identity pins its detector, model revision, prompt SHA-256, assembler, and a
pipeline-version fingerprint.

Read only published immutable PDF or image artifacts. Verify stored size and SHA-256 before
processing, then anchor a stable evaluation case to the artifact. Preserve exact model response
text in the component result and prediction document before passing it through a permissive legacy
adapter into strict Pydantic contracts. Invalid normalization becomes an inspectable failed
invocation and cannot create a scientific prediction.

Deterministically assemble valid candidate predictions into the existing western-blot structured
annotation and source-pixel spatial graph contracts. The retained model does not provide true band
coordinates, so v1 estimates panel, lane, protein-row, and band cells from candidate geometry and
emits an `estimated_geometry` warning. These regions are evidence anchors for review, not measured
band boundaries and not densitometry inputs.

Append one immutable prediction and one producer-linked pipeline publication for every successful
full run. Reuse the same case for later artifact reruns so independent human annotation revisions
remain unchanged. Selective component replay creates a new invocation; replaying assembly also
creates a new prediction and publication, while replaying an upstream component does not
implicitly execute descendants.

## Consequences

- Stored PDFs and images produce reviewable, provenance-linked cases without replacing historic
  model or reviewer state.
- Raw external output, normalized candidate predictions, review projections, and published results
  remain distinct representations.
- Prompt or model-revision changes identify a distinct pipeline version instead of silently
  changing an existing run.
- The original `hiveblot-ingest` filesystem workflow remains available as a compatibility path.
- Execution is synchronous until the generic job service in PRD-013; this service owns scientific
  orchestration, not leases or containers.
- True model- or detector-derived geometry, persisted crop artifacts, and deterministic
  densitometry remain later work.
