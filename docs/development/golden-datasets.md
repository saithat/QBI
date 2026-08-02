# Golden dataset development

Start PostgreSQL and MinIO, apply migrations by starting the API, and keep the services running:

```bash
docker compose up -d postgres minio
uv run uvicorn apps.api.main:app --host 127.0.0.1 --port 8080
```

The lifecycle API exposes:

- `POST|GET /api/v1/golden-datasets`
- `GET|DELETE /api/v1/golden-datasets/{dataset_id}`
- `POST /api/v1/golden-datasets/{dataset_id}/cases/{case_id}`
- `POST /api/v1/golden-datasets/{dataset_id}/cases/{case_id}/promotions`
- `GET /api/v1/golden-datasets/{dataset_id}/cases/{case_id}/transitions`
- `POST /api/v1/golden-datasets/{dataset_id}/snapshots`
- `GET /api/v1/golden-dataset-snapshots/{snapshot_id}`
- `POST|GET /api/v1/golden-dataset-snapshots/{snapshot_id}/exports`
- `POST /api/v1/golden-dataset-snapshots/{snapshot_id}/exports/{export_kind}/download-url`

Every mutating draft request carries an expected revision. A stale dataset revision or member state
version returns `409`. Add cases in one paper to only one split, then promote each case through the
explicit state machine. Selecting `reviewed` requires an immutable annotation revision; the
adjudicated path requires an adjudication selecting the same revision. Freeze only after every
active member is gold.

Run focused verification with:

```bash
uv run pytest \
  tests/unit/contracts/test_golden_dataset_contracts.py \
  tests/unit/evaluation/test_golden_dataset_service.py \
  tests/integration/test_golden_api.py
HIVEBLOT_RUN_LIVE_GOLDEN=1 \
  uv run pytest tests/integration/test_golden_live.py
```

The opt-in test requires PostgreSQL and MinIO values from the untracked file created by
`make local-env`. It uploads a
unique source artifact, creates and reviews a real evaluation case, checks paper/content leakage in
PostgreSQL, freezes and reloads the snapshot, publishes JSONL and manifest bytes to MinIO, validates
both against Pydantic, and downloads the manifest through an expiring signed URL.

Generate or verify public schemas with `make schemas` and `make schema-check`. The stable snapshot
and export schemas live under `packages/contracts/schemas/v1/`.
