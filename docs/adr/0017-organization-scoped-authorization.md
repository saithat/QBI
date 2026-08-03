# ADR 0017: Explicit organization scopes with backend policy enforcement

- Status: accepted
- Date: 2026-08-02

## Decision

Authenticate API callers with opaque bearer tokens whose plaintext is shown once and whose
HMAC-SHA-256 digest is stored in PostgreSQL. Resolve every protected resource to an explicit
`public` or `organization_private` scope, authorize that scope in backend services, and append an
allow/deny audit event for each authorization decision.

Keep user, organization, membership, token, and audit state in PostgreSQL. Add organization scope
to scientific and execution records that may contain customer-private data. Public cases may have
organization-private annotations, reviewer assignments, pipeline runs, traces, densitometry
results, and job records. Private cases require those child records to remain in the same
organization. Database constraints and triggers enforce the critical artifact, case, job, review,
pipeline, and publication relationships even if an application mapping is used incorrectly.

Use application authorization plus relational integrity in this version rather than PostgreSQL row
level security. The repository boundary centralizes scope resolution, while cross-tenant API and
live-database tests exercise both list and direct-access paths.

## Why

HiveBlot must let organizations reference shared public evidence without exposing their private
corrections, measurements, traces, or artifacts. Deriving all child visibility from a public paper
would leak private work; making a second copy of every public paper would fragment shared evidence.
Independent scoped records preserve both goals.

Opaque tokens avoid storing replayable credentials. A server-side pepper makes stolen token
digests insufficient on their own. Explicit scope columns make authorization inspectable and let
PostgreSQL reject cross-tenant provenance edges before they become durable.

## Consequences

- Deployed configuration must use bearer authentication and a secret token pepper of at least 32
  characters. Local and test environments may explicitly use disabled authentication.
- Public reads are available to authenticated users. Private reads require active membership in
  the matching organization; mutations additionally require the role-specific permission.
- A user with eligible access to exactly one organization creates corrections, assignments,
  generic pipeline runs, and densitometry runs privately by default on public cases. Multi-org
  users must choose a scope explicitly.
- Reviewer assignment locks are scoped, so two organizations can independently assign the same
  public case.
- Shared golden datasets and shared metric runs reject private cases and corrections.
- Row-level security remains a future defense-in-depth option. Direct database credentials must
  remain service-scoped and unavailable to end users.
- Rolling back PRD-017 requires resolving any active scoped assignments that would conflict under
  the earlier global assignment indexes.
