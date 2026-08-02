# ADR 0008: Keep pipeline attempts immutable and publish results explicitly

## Status

Accepted.

## Decision

Represent a scientific pipeline as an immutable, versioned directed acyclic graph of model and
tool components. Each component declares its output contract, configuration, producer identity,
and dependency keys. Register output contracts through the canonical Pydantic schema registry
before accepting a definition.

Creating a run materializes one pending invocation per component and resolves component dependency
keys to stable parent invocation UUIDs. A terminal result is written once. Successful results keep
raw and normalized output separately; schema-invalid normalized output becomes an immutable failed
result with the raw response, attempted normalization, and a validation issue intact. Execution and
cancellation failures use explicit failure kinds.

A selective replay creates a new pending invocation with a new trace and UUID and references the
attempt it replays. It never resets or overwrites the earlier invocation. Publication is a separate
operation: every selected final value names its producing successful invocation and JSON Pointer,
and must equal both that invocation's normalized result and the final normalized document.
Published evidence and output artifacts must already belong to the producing invocation or run.

Persist the complete canonical documents in PostgreSQL JSONB while also storing relational keys,
statuses, times, artifact associations, parent edges, replay edges, and trace IDs for integrity and
querying. API request and response models remain explicit HTTP adapters rather than aliases for
canonical or persistence models.

## Consequences

- Raw provider output, normalization attempts, validation failures, prior attempts, and replays are
  independently inspectable.
- Prompt, model, tool, component, and pipeline version changes produce distinguishable records.
- The workbench can compare run history without interpreting execution infrastructure.
- Database constraints prevent cross-run parent/replay edges, mismatched publication cases,
  duplicate artifact associations, and inconsistent pending/terminal timestamps.
- PRD-008 records and validates computation but does not schedule containers. Worker leases,
  cancellation, logs, Docker, and Kubernetes execution remain in PRD-013 and PRD-014.
