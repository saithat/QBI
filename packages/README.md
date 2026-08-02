# Shared packages

Shared process-boundary code lives here and must not import application or worker entry points.

- `contracts` contains strict Pydantic contracts and committed JSON Schemas.
- `storage` contains domain-independent immutable artifact publication and persistence adapters.

Tracing, authorization, and evaluation packages belong to later PRDs.
