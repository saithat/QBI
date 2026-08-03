# Distributed fetching development

## Configure

Fetch workers intentionally load a smaller settings surface than the API/model workers. Configure
PostgreSQL, S3, an identifying `DISCOVERY_USER_AGENT`, and an exact source allowlist:

```dotenv
FETCH_ALLOWED_HOSTS=pmc.ncbi.nlm.nih.gov
FETCH_WORKER_ID=fetch-worker-local
FETCH_LEASE_SECONDS=120
FETCH_MAX_ATTEMPTS=5
FETCH_HTTP_TIMEOUT_SECONDS=30
FETCH_MAX_RESPONSE_BYTES=250000000
FETCH_MAX_REDIRECTS=5
FETCH_DOMAIN_MINIMUM_INTERVAL_MILLISECONDS=1000
FETCH_DOMAIN_MAXIMUM_CONCURRENCY=2
```

An empty allowlist is rejected. Add only source hosts approved for acquisition; redirect targets
must be listed independently. Scaling workers does not modify the database-backed domain policy.

## Run locally

Start PostgreSQL and MinIO, discover records, then run one fetch lease:

```bash
docker compose up -d postgres minio
uv run hiveblot-discover-pmc --from-date 2026-08-01 --until-date 2026-08-02 \
  --maximum-pages 1
uv run hiveblot-fetch-worker --once
```

For a long-lived Compose worker:

```bash
docker compose --profile workers up fetch-worker
```

Inspect queue provenance and metrics through:

```bash
curl http://127.0.0.1:8080/api/v1/crawl-fetch-tasks/TASK_UUID
curl http://127.0.0.1:8080/api/v1/crawl-fetch-tasks/TASK_UUID/attempts
curl http://127.0.0.1:8080/api/v1/crawl/metrics
```

## Verify

```bash
uv run pytest -p no:cacheprovider tests/unit/crawler/test_http_fetcher.py \
  tests/unit/crawler/test_fetch_worker.py \
  tests/integration/test_fetch_rate_limit.py \
  tests/integration/test_fetching_api.py
```

The simulated HTTP source test starts two workers sharing one in-memory coordination backend and
asserts that requests to one domain remain separated by the configured interval. The opt-in
PostgreSQL test covers concurrent permits, leases, worker loss, Retry-After, dead letters,
conditional recrawl, artifact linkage, outbox publication, metrics, and strict record mapping:

```bash
HIVEBLOT_RUN_LIVE_FETCH=1 \
DATABASE_URL=postgresql://USER:PASSWORD@127.0.0.1:5432/hiveblot \
uv run pytest -p no:cacheprovider tests/integration/test_fetching_live.py
```

## KEDA

Install KEDA 2.20 or a compatible release, deploy the base, then apply the optional scaler:

```bash
kubectl apply -k infra/kubernetes/base
kubectl apply -k infra/kubernetes/addons/keda
```

Create the untracked `hiveblot-keda-secrets` Secret with a read-only PostgreSQL `DATABASE_URL` that
can execute the queue-depth query. The scaler targets five eligible tasks per replica and keeps one
worker to discover/enqueue newly eligible frontier rows. KEDA is not installed automatically and
remains an externally managed cluster component.
