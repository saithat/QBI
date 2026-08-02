# ADR 0002: Content-addressed artifact storage

- Status: accepted
- Date: 2026-08-02
- PRD: 002

## Decision

HiveBlot stores immutable bytes under deterministic S3 keys derived from SHA-256. PostgreSQL stores
artifact metadata, upload state, visibility scope, relationships, and events. Multipart and source
ingestion write only to a staging prefix until the complete byte stream has been hashed and its
media type has been detected from content.

Byte identity and access metadata are intentionally separate:

- `artifact_blobs` is globally unique by SHA-256 and points to one object-store key.
- `artifacts` is unique by SHA-256 plus visibility/organization scope.

This permits physical byte deduplication while preventing the metadata of a private organization
from becoming the canonical record returned to another organization. Repeated ingestion inside
one scope returns the existing artifact ID and records a deduplication event.

## Consequences

- Published object keys are never accepted from callers and are never updated in place.
- Upload sessions remain independently queryable when interrupted, failed, or aborted.
- Publication can resume after a process failure because validated hash/type/size are stored before
  the final database transaction.
- Direct object access uses short-lived signed URLs; the API handles metadata, not large downloads.
- Authorization decisions are deferred to PRD-017, but visibility invariants already exist in
  contracts and database constraints.
