# Review queue development

Start PostgreSQL and MinIO, then run HiveBlot:

```bash
docker compose up --build -d postgres minio
uv run uvicorn apps.api.main:app --host 127.0.0.1 --port 8080
```

Open <http://localhost:8080/review>. The browser stores a development reviewer UUID in local
storage. Filters are encoded in the query string, so copying the address or refreshing reproduces
the same view. Selecting a case also records its UUID in the URL.

Verify the browser/API boundary and the real 10,000-case query:

```bash
uv run pytest tests/integration/test_review_queue_api.py
HIVEBLOT_RUN_LIVE_REVIEW_QUEUE=1 \
  uv run pytest tests/integration/test_review_queue_live.py
```

The live test applies migrations, seeds an isolated dataset, verifies bounded pagination and
exclusive assignment conflicts, then removes every seeded record. It never publishes object bytes.
