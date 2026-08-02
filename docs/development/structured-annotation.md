# Structured annotation development

Start PostgreSQL, MinIO, and the API:

```bash
docker compose up --build -d postgres minio
uv run uvicorn apps.api.main:app --host 127.0.0.1 --port 8080
```

Open a seeded case from `/review` and choose **Edit annotation**, or navigate directly to
`/annotate/<case-id>`. The browser stores a local reviewer UUID until authentication arrives in
PRD-017.

Verification commands:

```bash
node --check apps/web/assets/annotate.js
uv run pytest tests/integration/test_structured_editor_api.py
HIVEBLOT_RUN_LIVE_STRUCTURED_EDITOR=1 \
  uv run pytest tests/integration/test_structured_editor_live.py
```

The live test applies migration `0006`, writes complete typed snapshots through PostgreSQL JSONB,
rejects a stale expected head, and restores an older snapshot by appending a third revision. Use a
normalized `WesternBlotStructuredAnnotation` prediction to exercise individual and bulk acceptance;
legacy prediction JSON remains visible but is marked incompatible until PRD-011 migrates extraction.

Autosave waits for edits to settle. Invalid documents remain in the browser with the server's
Pydantic error and are not committed. A failed validation or concurrency check does not retry in a
loop; another edit or **Save now** triggers the next attempt.
