# Development setup and verification

## Quality-only setup

Requirements: Python 3.12 and uv.

```bash
uv sync --locked --all-extras
make check
```

The default suite needs no running services. Live integration checks are opt-in and are skipped
unless their flags are enabled; [the test inventory](../../tests/README.md) lists every flag.

Individual gates:

```bash
make lint
make typecheck
make schema-check
make test
uv build
```

When a public contract changes intentionally:

```bash
make schemas
git diff -- packages/contracts/schemas/v1
make schema-check
```

Do not edit schema snapshots by hand.

## Full local runtime

Generate a private `.env`, set the host Hugging Face cache path in that ignored file, and verify
NVIDIA Docker support before starting Compose. The generator creates independent random local
credentials, writes the file with mode `0600`, and refuses to overwrite an existing file:

```bash
make local-env
docker compose config
make up
docker compose ps
curl http://localhost:8080/health
```

Ingest an existing local PDF with `make ingest PDF=<filename>`. Inputs live under `data/input/`
and resumable results under `data/runs/`; both are ignored.

PostgreSQL and MinIO can run without the GPU stack. The following live checks use the credentials
from the generated `.env` explicitly because most fixtures disable automatic dotenv loading.
Run these against local test data; they apply migrations and create test records:

```bash
docker compose up --build -d postgres minio
HIVEBLOT_RUN_LIVE_STORAGE=1 \
  uv run --env-file .env pytest tests/integration/test_artifact_storage_live.py
HIVEBLOT_RUN_LIVE_EVALUATION=1 \
  uv run --env-file .env pytest tests/integration/test_evaluation_storage_live.py
HIVEBLOT_RUN_LIVE_REVIEW_QUEUE=1 \
  uv run --env-file .env pytest tests/integration/test_review_queue_live.py
HIVEBLOT_RUN_LIVE_WORKBENCH=1 \
  uv run --env-file .env pytest tests/integration/test_workbench_live.py
HIVEBLOT_RUN_LIVE_STRUCTURED_EDITOR=1 \
  uv run --env-file .env pytest tests/integration/test_structured_editor_live.py
HIVEBLOT_RUN_LIVE_SPATIAL_EDITOR=1 \
  uv run --env-file .env pytest tests/integration/test_spatial_editor_live.py
HIVEBLOT_RUN_LIVE_PIPELINE=1 \
  uv run --env-file .env pytest tests/integration/test_pipeline_registry_live.py
HIVEBLOT_RUN_LIVE_METRICS=1 \
  uv run --env-file .env pytest tests/integration/test_metrics_live.py
HIVEBLOT_RUN_LIVE_GOLDEN=1 \
  uv run --env-file .env pytest tests/integration/test_golden_live.py
HIVEBLOT_RUN_LIVE_EXTRACTION=1 \
  uv run --env-file .env pytest tests/integration/test_extraction_live.py
HIVEBLOT_RUN_LIVE_DENSITOMETRY=1 \
  uv run --env-file .env pytest tests/integration/test_densitometry_live.py
HIVEBLOT_RUN_LIVE_JOBS=1 \
  uv run --env-file .env pytest tests/integration/test_job_service_live.py
HIVEBLOT_RUN_LIVE_DISCOVERY=1 \
  uv run --env-file .env pytest tests/integration/test_discovery_live.py
HIVEBLOT_RUN_LIVE_FETCH=1 \
  uv run --env-file .env pytest tests/integration/test_fetching_live.py
```

Authorization and evidence-search acceptance tests require separate isolated databases. Use the
explicit database commands in [authorization setup](authorization.md) and
[evidence-search setup](evidence-search.md) for `HIVEBLOT_RUN_LIVE_AUTH` and
`HIVEBLOT_RUN_LIVE_SEARCH`.

See `docs/development/artifact-storage.md` for the multipart API flow and
`docs/development/review-queue.md`, `docs/development/source-workbench.md`, and
`docs/development/structured-annotation.md`, `docs/development/spatial-annotation.md`, and
`docs/development/pipeline-registry.md`, `docs/development/evaluation-metrics.md`, and
`docs/development/job-service.md` for browser and worker verification.
Public-source adapter, frontier, and scheduled-discovery verification is documented in
`docs/development/public-discovery.md`. Queue-backed acquisition, shared source limits, and KEDA
verification are documented in `docs/development/distributed-fetching.md`.
Identity bootstrap, bearer-mode setup, tenant isolation, audit inspection, and the isolated live
authorization test are documented in `docs/development/authorization.md`.
Versioned index builds, cited search, frozen retrieval evaluation, and the isolated live search
test are documented in `docs/development/evidence-search.md`.

For the local Kubernetes runtime and the full artifact-to-Kubernetes-Job acceptance smoke, see
`docs/development/kubernetes.md`.

The tracked `.env.example`, `.env.test.example`, and `.env.deployed.example` files intentionally
contain no credential values. Do not copy production credentials into a tracked template. If a
generated local credential has been used anywhere beyond the loopback-only development stack,
rotate it rather than reusing it.

## Baseline verification

```bash
uv run pytest tests/baseline/test_hackathon_extraction_baseline.py
sha256sum tests/fixtures/baseline/*.json
```

The manifest distinguishes uncommitted source bytes from committed raw model and normalized
outputs. Update this baseline only after a reviewed, intentional behavior change.
