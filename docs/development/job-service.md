# Local generic job development

## Prerequisites

- Python 3.12 and `uv`
- Docker Engine accessible to the host worker
- local PostgreSQL and MinIO

Start durable dependencies and build the worker image:

```bash
docker compose up -d postgres minio
docker build -t hiveblot:prd-013 .
```

Apply migrations through the normal application initializer and run one polling cycle:

```bash
uv run python -c "from hiveblot import db; from hiveblot.settings import get_settings; db.initialize(get_settings().database_url)"
uv run hiveblot-job-worker --once
```

Run `uv run hiveblot-job-worker` without `--once` for a long-lived local worker. It intentionally
runs on the host so its temporary bind-mount paths are visible to the same Docker daemon. Do not
mount an unrestricted Docker socket into the API container. In a Pod, use
`hiveblot-job-worker --executor kubernetes-job`; see `docs/development/kubernetes.md`.

Submit canonical specifications through `POST /api/v1/jobs`. The HTTP body wraps the strict
contract as `{"schema_version":"1.0","specification":{...}}`. Job factories owned by a scientific
worker, such as `workers.jobs.specifications.legacy_normalization_job`, create versioned domain job
specifications without adding domain concepts to the scheduler.

Inspect state through:

- `GET /api/v1/jobs?status=pending`
- `GET /api/v1/jobs/{job_id}`
- `GET /api/v1/jobs/{job_id}/attempts`
- `GET /api/v1/job-attempts/{attempt_id}/logs`
- `POST /api/v1/jobs/{job_id}/cancel`

## Verification

The normal suite uses an in-memory repository and a recording Docker runner:

```bash
make schemas
make check
```

Run the PostgreSQL/MinIO state-machine test:

```bash
HIVEBLOT_RUN_LIVE_JOBS=1 \
  uv run pytest -p no:cacheprovider tests/integration/test_job_service_live.py
```

Run the actual restricted container path after building `hiveblot:prd-013`:

```bash
HIVEBLOT_RUN_LIVE_JOBS=1 \
HIVEBLOT_RUN_DOCKER_JOBS=1 \
HIVEBLOT_JOB_TEST_IMAGE=hiveblot:prd-013 \
  uv run pytest -p no:cacheprovider tests/integration/test_job_service_live.py
```

The Docker acceptance test stores the historic model-output fixture as an immutable input, leases
the job from PostgreSQL, runs `western-blot-normalize` with no network, publishes the deterministic
JSON output to MinIO, and verifies all 40 preserved records and captured logs.

The Kubernetes acceptance path exercises the same operation through `make kind-smoke` after
`make kind-up`.
