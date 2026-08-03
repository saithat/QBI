# Authorization development and verification

Local development defaults to `AUTHENTICATION_MODE=disabled`, which creates an explicit system
principal for local/test use. Deployed settings reject disabled authentication. To exercise bearer
authorization locally, generate the ignored environment first, then change the mode in `.env`:

```bash
make local-env
# Set AUTHENTICATION_MODE=bearer in the ignored .env file.
docker compose up --build -d postgres minio
```

Apply migrations by starting the API or another normal application entry point. Bootstrap an
initial user, organization, administrator membership, and expiring token:

```bash
uv run hiveblot-auth-bootstrap \
  --email scientist@example.test \
  --display-name "Local Scientist" \
  --organization-slug local-lab \
  --organization-name "Local Lab" \
  --role organization_administrator \
  --token-label local-development \
  --expires-days 30
```

The command prints the bearer token once. Store it in an appropriate local secret manager; do not
write it to a tracked file, shell script, issue, or test fixture. HiveBlot persists only its keyed
digest. Use the token as `Authorization: Bearer <token>` and inspect the resolved identity with
`GET /api/v1/auth/me`.

Organization administrators can create organizations, set memberships, and inspect bounded audit
history through `/api/v1/organizations` routes. Cross-tenant direct reads are concealed as `404`;
unauthorized mutations return `403`. Invalid resource-scope relationships return a validation or
conflict response and are also rejected by PostgreSQL.

Run deterministic policy and API tests without external services:

```bash
uv run pytest -p no:cacheprovider tests/unit/auth tests/integration/test_authorization_api.py
```

Run the PostgreSQL constraint acceptance test only against an isolated database:

```bash
HIVEBLOT_RUN_LIVE_AUTH=1 \
DATABASE_URL=postgresql://USER:PASSWORD@127.0.0.1:5432/hiveblot_auth_test \
AUTHENTICATION_MODE=disabled \
uv run pytest -p no:cacheprovider tests/integration/test_authorization_live.py
```

The live test applies all migrations, authenticates two organizations, verifies allow/deny audit
events, and exercises cross-tenant artifact, job, reviewer-assignment, pipeline-run, trace-output,
and provenance constraints. Never point it at a shared or production database.

## Secret-leak response

If a scanner reports a credential, revoke or rotate it first. Removing a value in a later commit
does not remove it from existing Git history. Coordinate any history rewrite with every repository
consumer because rewritten commit identifiers require force-updating branches and fresh clones.
Tracked environment examples must remain blank; `make local-env` creates random local values in a
gitignored mode-`0600` file and refuses to overwrite it.
