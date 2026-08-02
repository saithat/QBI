# Development setup and verification

## Quality-only setup

Requirements: Python 3.12 and uv.

```bash
uv sync --locked --all-extras
make check
```

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

Copy `.env.example` to `.env`, set the host Hugging Face cache path, and verify NVIDIA Docker
support before starting Compose:

```bash
cp .env.example .env
docker compose config
make up
docker compose ps
curl http://localhost:8080/health
```

Ingest an existing local PDF with `make ingest PDF=<filename>`. Inputs live under `data/input/`
and resumable results under `data/runs/`; both are ignored.

PostgreSQL and MinIO can run without the GPU stack:

```bash
docker compose up --build -d postgres minio
HIVEBLOT_RUN_LIVE_STORAGE=1 uv run pytest tests/integration/test_artifact_storage_live.py
HIVEBLOT_RUN_LIVE_EVALUATION=1 \
  uv run pytest tests/integration/test_evaluation_storage_live.py
HIVEBLOT_RUN_LIVE_REVIEW_QUEUE=1 \
  uv run pytest tests/integration/test_review_queue_live.py
HIVEBLOT_RUN_LIVE_WORKBENCH=1 \
  uv run pytest tests/integration/test_workbench_live.py
HIVEBLOT_RUN_LIVE_STRUCTURED_EDITOR=1 \
  uv run pytest tests/integration/test_structured_editor_live.py
HIVEBLOT_RUN_LIVE_SPATIAL_EDITOR=1 \
  uv run pytest tests/integration/test_spatial_editor_live.py
HIVEBLOT_RUN_LIVE_PIPELINE=1 \
  uv run pytest tests/integration/test_pipeline_registry_live.py
HIVEBLOT_RUN_LIVE_METRICS=1 \
  uv run pytest tests/integration/test_metrics_live.py
HIVEBLOT_RUN_LIVE_JOBS=1 \
  uv run pytest tests/integration/test_job_service_live.py
```

See `docs/development/artifact-storage.md` for the multipart API flow and
`docs/development/review-queue.md`, `docs/development/source-workbench.md`, and
`docs/development/structured-annotation.md`, `docs/development/spatial-annotation.md`, and
`docs/development/pipeline-registry.md`, `docs/development/evaluation-metrics.md`, and
`docs/development/job-service.md` for browser and worker verification.

## Baseline verification

```bash
uv run pytest tests/baseline/test_hackathon_extraction_baseline.py
sha256sum tests/fixtures/baseline/*.json
```

The manifest distinguishes uncommitted source bytes from committed raw model and normalized
outputs. Update this baseline only after a reviewed, intentional behavior change.
