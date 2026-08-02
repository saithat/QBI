# Evaluation persistence development

Start PostgreSQL and MinIO, then run the opt-in persistence acceptance test:

```bash
docker compose up --build -d postgres minio
HIVEBLOT_RUN_LIVE_EVALUATION=1 \
  uv run pytest tests/integration/test_evaluation_storage_live.py
```

The test publishes a small source artifact, applies migrations, creates an evaluation case, stores
two predictions and two independent reviews, appends a revision, confirms a stale edit conflict,
and adjudicates the reviews. Published test artifacts and provenance are intentionally retained in
the local development volumes.

Relevant API roots:

- `/api/v1/evaluation-cases`
- `/api/v1/predictions`
- `/api/v1/annotations`
- `/api/v1/annotation-revisions`
- `/api/v1/assignments`
- `/api/v1/annotation-error-codes`
- `/api/v1/evaluation-cases/{id}/adjudications`

No endpoint updates or deletes a prediction or revision. To edit, send the full next snapshot with
the current head revision UUID. A `409` means the caller must reload before retrying.
