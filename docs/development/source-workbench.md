# Source workbench development

Start durable dependencies and the local API:

```bash
docker compose up --build -d postgres minio
uv run uvicorn apps.api.main:app --host 127.0.0.1 --port 8080
```

Open `/review`, select a case, and choose **Open evidence**. The workbench route is
`/workbench/<case-id>`.

Verify its static/API boundary and live object-store path:

```bash
node --check apps/web/assets/workbench.js
uv run pytest tests/integration/test_workbench_api.py
HIVEBLOT_RUN_LIVE_WORKBENCH=1 \
  uv run pytest tests/integration/test_workbench_live.py
```

The live test publishes a valid deterministic PNG, creates a source-pixel prediction region,
appends two caption/context revisions, rejects a stale expected head, builds the workbench,
downloads through an expiring MinIO URL, and checks the exact SHA-256 bytes. Its temporary
evaluation case is removed; the immutable deduplicated artifact remains available for later runs.

Missing or undecodable sources remain explicit in the viewer. Large bytes must continue to use the
signed URL flow; do not add a FastAPI byte-proxy route.
