# Organization authorization architecture

HiveBlot separates identity, authorization policy, domain behavior, and persistence mapping. HTTP
handlers obtain an authenticated principal, ask the authorization service for a scoped decision,
and only then invoke the domain service. UI state is never authoritative for access control.

```text
opaque bearer token
        |
keyed token digest lookup + active user/memberships
        |
authenticated principal
        |
resource scope lookup ---- public / organization_private + organization_id
        |
role permission decision ---- append-only allow/deny audit event
        |
domain service ---- strict Pydantic contract ---- persistence mapping
        |
PostgreSQL scope constraints + immutable S3 artifact references
```

## Roles and permissions

| Role | Read private data | Review and annotate | Manage evidence/pipelines | Submit/cancel jobs | Manage organization/audit |
|---|---:|---:|---:|---:|---:|
| Organization administrator | Yes | Yes | Yes | Yes | Yes |
| Scientist | Yes | Yes | Yes | Yes | No |
| Reviewer | Yes | Yes | No | No | No |
| Read-only | Yes | No | No | No | No |

All roles are limited to active memberships in the resource organization. Public reads require an
authenticated principal but no organization membership. Public mutations require the corresponding
permission through at least one active membership.

## Scoped resources

Artifacts, evaluation cases, annotations, adjudications, reviewer assignments, pipeline runs and
publications, traces, jobs, attempts, logs, and outputs resolve to a stable scope. Predictions inherit
their case scope. Component invocations inherit their pipeline-run scope. Job attempts and logs
inherit their job scope.

A private child may reference public source evidence. A public child may not reference a private
artifact. Private output artifacts must exactly match the producing run or job organization.
Reviewer annotations, assignments, and densitometry attempts on public evidence remain private by
default, while the shared paper and figure remain public.

List endpoints apply the same scope policy as direct reads. Review-queue projections filter private
reviewers and error categories in SQL. Workbench and densitometry projections filter private
reviewer/adjudication overlays and attempts before issuing signed artifact URLs.

## Credentials and auditing

The bootstrap command generates an opaque token and prints it once. PostgreSQL stores only a keyed
digest, token metadata, expiration, revocation timestamp, and last-use timestamp. The token pepper
comes from secret configuration and is never stored in a public contract.

Authorization audit events include actor, token identifier, action, outcome, target, organization,
request identifier, reason, and timestamp. A PostgreSQL trigger makes the table append-only.
Sensitive artifact URL issuance records the authenticated actor through the existing artifact event
log as well.

## Database integrity

Migration `0014_organization_authorization` adds scope checks and organization foreign keys. Triggers
reject cross-organization artifact relationships, case sources, annotations, adjudications, review
assignments, pipeline inputs/outputs/evidence/publications, and job inputs/outputs. These constraints
supplement backend authorization; they do not replace it.
