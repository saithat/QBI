# Local artifact storage

Docker Compose builds the security-fixed upstream MinIO tag from source and exposes its S3 API on
`127.0.0.1:9000` and console on `127.0.0.1:9001`. Run `make local-env` once to create untracked,
random credentials shared by MinIO and the local application. The tracked template contains no
credential values.

Start PostgreSQL and MinIO without the GPU model stack:

```bash
docker compose up --build -d postgres minio
```

Use `uv run uvicorn apps.api.main:app --host 127.0.0.1 --port 8080` for an API process configured
from `.env`, or start the complete stack with `make up`. The bucket is created lazily on the first
artifact request.

Multipart flow:

1. `POST /api/v1/artifact-uploads` with filename, expected size, part count, and visibility.
2. PUT each part directly to the returned signed URL; retain each response ETag.
3. `POST /api/v1/artifact-uploads/{upload_id}/complete` with the ordered part/ETag list.
4. Fetch metadata with `GET /api/v1/artifacts/{artifact_id}`.
5. Request a short-lived download with `POST /api/v1/artifacts/{artifact_id}/download-url`.

HTTP source ingestion is disabled by default. Set `SOURCE_INGEST_ALLOWED_HOSTS` to a comma-separated
exact host list, then use `POST /api/v1/artifacts/source-ingestions`. This adapter is intentionally
not a crawler and does not perform discovery.

Run focused tests with:

```bash
uv run pytest tests/unit/storage tests/integration/test_artifact_api.py
```
