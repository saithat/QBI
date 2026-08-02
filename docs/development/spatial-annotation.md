# Spatial annotation development

Start PostgreSQL, MinIO, and the current API:

```bash
docker compose up --build -d postgres minio
uv run uvicorn apps.api.main:app --host 127.0.0.1 --port 8080
```

Open a seeded case from `/review` and choose **Edit geometry**, or navigate to
`/spatial/<case-id>`. Geometry editing requires an image or rendered-page artifact. PDFs are shown
read-only; the API never proxies their bytes. Until PRD-017, the browser keeps a local reviewer UUID
in `localStorage`.

Useful controls:

- `V` selects, `N` enters create mode, `P` pans, and Delete removes selected regions.
- Drag a region or its corner handles; arrow keys nudge by one source pixel and Shift+arrow by ten.
- Shift-click selects several regions; **Group** adds stable grouping relationships.
- **Split** replaces one region with two new stable regions and records their grouping.
- `[` and `]`, or the lane arrows, rewrite validated `precedes` edges.
- Ctrl/Cmd+S saves immediately; settled changes autosave as immutable revisions.

Verification commands:

```bash
node --check apps/web/assets/spatial.js
uv run pytest tests/unit/contracts/test_spatial_editing_contracts.py \
  tests/unit/evaluation/test_spatial_annotation_service.py \
  tests/integration/test_spatial_editor_api.py
HIVEBLOT_RUN_LIVE_SPATIAL_EDITOR=1 \
  uv run pytest tests/integration/test_spatial_editor_live.py
```

The live test publishes a real 400×200 PNG to MinIO, persists a complete nine-region graph in
PostgreSQL, checks prediction projection, appends a corrected revision, rejects a stale head, and
restores old geometry without mutating history. For manual browser verification, confirm the source
loads from its signed URL, resize or create a region, reorder lanes, wait for the revision number to
increase, and reload to confirm the same source-pixel geometry returns.

Invalid drafts stay in the browser after a `422` response so the reviewer can correct them. A `409`
means another revision won the optimistic-concurrency race; this editor does not silently merge or
overwrite it.
