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
```

See `docs/development/artifact-storage.md` for the multipart API flow.

## Baseline verification

```bash
uv run pytest tests/baseline/test_hackathon_extraction_baseline.py
sha256sum tests/fixtures/baseline/*.json
```

The manifest distinguishes uncommitted source bytes from committed raw model and normalized
outputs. Update this baseline only after a reviewed, intentional behavior change.
