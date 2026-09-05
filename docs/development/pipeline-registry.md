# Pipeline registry development

Start PostgreSQL, MinIO, and the local API:

```bash
docker compose up --build -d postgres minio
uv run uvicorn apps.api.main:app --host 127.0.0.1 --port 8080
```

The registry API exposes:

- `POST|GET /api/v1/pipeline-definitions`
- `GET /api/v1/pipeline-definitions/{definition_id}`
- `POST|GET /api/v1/evaluation-cases/{case_id}/pipeline-runs`
- `GET /api/v1/pipeline-runs/{run_id}`
- `POST /api/v1/component-invocations/{invocation_id}/results`
- `POST /api/v1/component-invocations/{invocation_id}/replays`
- `POST /api/v1/pipeline-runs/{run_id}/publications`

A reported success can return HTTP 200 with invocation status `failed` when strict output-schema
validation fails. This is deliberate: the report was accepted and preserved, but the attempted
output did not enter validated scientific state. Reposting a terminal result returns `409`.

Open `/workbench/<case-id>` and select **Runs** to inspect versioned attempts, validation failures,
replays, published snapshots, raw output, normalized output, costs, latency, and trace IDs.

Verification commands:

```bash
uv run pytest tests/unit/contracts/test_pipeline_run_contracts.py \
  tests/unit/evaluation/test_pipeline_registry_service.py
node --check apps/web/assets/workbench.js
HIVEBLOT_RUN_LIVE_PIPELINE=1 \
  uv run --env-file .env pytest tests/integration/test_pipeline_registry_live.py
```

The opt-in acceptance test publishes source and output artifacts to MinIO, applies migration
`0007`, persists a two-component run, records malformed normalized output as a failure, replays only
that component, publishes the corrected result, and reconstructs all history through a fresh
PostgreSQL repository instance.
