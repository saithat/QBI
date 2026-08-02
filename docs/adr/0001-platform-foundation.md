# ADR-0001: Establish strict contracts around the retained prototype

- Status: Accepted
- Date: 2026-08-02
- Decision scope: PRD-001

## Context

The hackathon repository has a useful end-to-end local western blot extractor but combines
application schemas, model output, raw dictionaries, persistence rows, environment parsing, and
local run files. A wholesale rewrite would discard working behavior and make scientific drift
hard to detect. Implementing artifact storage or distributed execution now would also cross PR
boundaries.

## Decision

1. Preserve the current extraction implementation and prompt behavior behind a canonical worker
   entry point.
2. Capture one real historic model response and its exact normalized output as a baseline.
3. Introduce `hiveblot_contracts` as an application-independent Pydantic v2 package.
4. Make canonical contracts strict, frozen, extra-forbid, explicitly versioned, and backed by
   committed deterministic JSON Schemas.
5. Separate HTTP models, model predictions, domain criteria, persistence records, and job queue
   records with explicit mappings.
6. Replace dataclass/environment parsing with Pydantic Settings and explicit environment modes.
7. Keep compatibility imports and root Docker/Compose commands while introducing target
   `apps/`, `workers/`, `packages/`, `services/`, `infra/`, `tests/`, and `docs/` boundaries.

## Consequences

New process interfaces can depend on stable schemas without importing FastAPI or persistence
code. Unknown fields and silent internal coercion fail early. Schema drift becomes visible in
CI. Existing local extraction remains usable and testable.

The codebase is intentionally transitional: scientific extraction output is not yet fully
canonical or provenance-registered, and local files are not immutable artifacts. Those changes
must occur in their owning PRDs with explicit migration and evaluation work.
