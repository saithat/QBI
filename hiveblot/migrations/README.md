# Database migrations

`*.up.sql` files are applied once in lexical order by `hiveblot.migrations.apply_migrations`.
Matching `*.down.sql` files document a practical manual rollback. Rollbacks are intentionally not
run automatically because deleting published provenance records is destructive.

`0012_public_discovery_frontier` adds immutable discovery runs/observations, canonical frontier
records and aliases, typed source relationships, artifact-acquisition provenance, optimistic
scheduling state, and append-only frontier events.

`0013_distributed_fetching` adds durable fetch tasks/attempts, shared domain policies and request
permits, robots cache state, conditional-request metadata, downstream parse outbox records, and
fetch metrics inputs. Its down migration removes those six tables before removing the four added
frontier columns.

`0014_organization_authorization` adds users, organizations, memberships, opaque-token digests,
append-only authorization events, organization scope for scientific/execution records, scoped
reviewer-assignment locks, and cross-resource scope triggers. Its down migration performs a
preflight and refuses without mutation when active scoped assignments would violate the earlier
global assignment indexes. Existing private artifact organization UUIDs are preserved as active
`legacy-<uuid>` organization records so their foreign-key relationships remain valid.

`0015_evidence_search_retrieval` adds immutable search configurations and index versions,
provenance-linked evidence documents and artifact citations, generated PostgreSQL full-text
vectors, structured-filter indexes, frozen retrieval datasets, and append-only evaluation runs.
It also adds the reserved platform-operator identity used by short-lived search-management tokens
and prevents that identity from joining an organization. Triggers enforce source/citation scope
and immutable artifact metadata, JSON/relational agreement, configured embedding dimensions,
completed-index immutability, legal lifecycle transitions, and a passing evaluation before
activation.
