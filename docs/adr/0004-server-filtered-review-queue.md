# ADR 0004: Server-filtered review queue with URL-owned browser state

## Status

Accepted.

## Decision

The initial evaluation browser uses a PostgreSQL read projection behind strict Pydantic contracts.
Filtering, counts, and bounded pagination execute on the server. The browser represents active
filters, page offset, and selected case in the URL; saved views persist the same canonical filter
contract. Reviewer assignment reuses PRD-003's database-serialized assignment service.

The browser is shipped as dependency-free HTML, CSS, and JavaScript under `apps/web`. It resolves
thumbnails through expiring artifact URLs and never asks FastAPI to stream the large source object.
The locally generated reviewer UUID is an explicit development placeholder, not authentication.

## Consequences

- Links and refreshes reproduce the active queue state.
- A page materializes at most 200 summaries even when the dataset has many thousands of cases.
- Query projections use indexed per-case lateral lookups so newly loaded datasets do not depend on
  autovacuum statistics for acceptable performance.
- Gold eligibility and regression status are projection metadata only; their lifecycle remains in
  later PRDs.
- Authentication and authoritative reviewer identity remain deferred to PRD-017.
