# Public discovery development

PRD-015's normal test suite requires no services or live PMC access; adapter and artifact tests use
in-memory protocol/storage fixtures. An intentional discovery run requires PostgreSQL plus an
S3-compatible object store because exact source responses are published before normalization. It
does not require a model server or Kubernetes.

## Configuration

Set a descriptive, monitored project URL or contact in `DISCOVERY_USER_AGENT`. The local default is
intentionally obvious and deployed settings reject it. Other discovery settings bound the official
endpoint, HTTP timeout, response bytes, lookback window, and page count:

```dotenv
PMC_OAI_BASE_URL=https://pmc.ncbi.nlm.nih.gov/api/oai/v1/mh/
DISCOVERY_USER_AGENT=HiveBlot/0.1 (+https://github.com/saithat/QBI)
DISCOVERY_HTTP_TIMEOUT_SECONDS=30
DISCOVERY_MAX_RESPONSE_BYTES=5000000
DISCOVERY_LOOKBACK_DAYS=2
DISCOVERY_MAXIMUM_PAGES=20
```

Do not use the PMC article web interface for automated discovery. The adapter targets the official
OAI endpoint and treats article licenses as unverified until separately checked.

## Run discovery

Start PostgreSQL and MinIO, apply migrations through any application entry point, then run:

```bash
docker compose up -d postgres minio
uv run hiveblot-discover-pmc --from-date 2026-08-01 --until-date 2026-08-02 \
  --maximum-pages 2
```

Each raw OAI response is first published as an immutable public artifact and foreign-keyed from the
discovery run. The command writes one JSON summary to stdout. `next_cursor` is null when the listing
finished. If a bounded run returns a cursor, resume it explicitly:

```bash
uv run hiveblot-discover-pmc --cursor 'opaque-source-token' --maximum-pages 20
```

Do not combine a cursor with date arguments. Discovery only populates the frontier; it does not
acquire the full paper/source-data records it lists.

## Inspect and schedule the frontier

With the API running:

```bash
curl 'http://127.0.0.1:8080/api/v1/discovery-frontier?status=pending&missing_artifact=true'
curl 'http://127.0.0.1:8080/api/v1/discovery-frontier/FRONTIER_UUID'
```

Schedule and retry endpoints require `schema_version: "1.0"` plus the current optimistic version.
A stale version returns HTTP 409. Artifact acquisition links are accepted only for already
published immutable artifact metadata.

## Tests

```bash
uv run pytest -p no:cacheprovider tests/unit/crawler \
  tests/unit/contracts/test_discovery_contracts.py \
  tests/integration/test_discovery_api.py
```

The PostgreSQL repository test is opt-in:

```bash
HIVEBLOT_RUN_LIVE_DISCOVERY=1 \
DATABASE_URL=postgresql://USER:PASSWORD@127.0.0.1:5432/hiveblot \
uv run pytest -p no:cacheprovider tests/integration/test_discovery_live.py
```

It uses unique record IDs and removes only its own rows. It tests a real up-migrated database,
identity/URL deduplication, batch idempotency, relationships, optimistic scheduling, acquisition
linkage, and strict JSON mapping.

## Kubernetes schedule

Render the base to inspect the bounded CronJob:

```bash
kubectl kustomize infra/kubernetes/base
kubectl kustomize infra/kubernetes/overlays/kind
```

The base schedules daily PMC discovery. The kind overlay sets `suspend: true`; remove that patch
only when intentionally testing external discovery with a suitable identifying user agent.
