# Shared packages

Shared process-boundary code lives here and must not import application or worker entry points.

- `contracts` contains strict Pydantic contracts and committed JSON Schemas.
- `evaluation` contains case, prediction, append-only review, assignment, and adjudication services.
- `extraction` contains western-blot stage adapters, strict normalization, and orchestration.
- `storage` contains domain-independent immutable artifact publication and persistence adapters.

Tracing and authorization packages belong to later PRDs.
