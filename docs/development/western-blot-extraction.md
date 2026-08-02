# Western-blot extraction development

Start PostgreSQL, MinIO, the local model server, and the API using the normal Compose profile, or
run PostgreSQL and MinIO separately while pointing `VLLM_BASE_URL` at an OpenAI-compatible local
vision endpoint:

```bash
make local-env
docker compose up --build -d postgres minio vllm
uv run uvicorn apps.api.main:app --host 127.0.0.1 --port 8080
```

For deployed configuration, set `VLLM_MODEL_REVISION` to an immutable provider revision. Local
development defaults to `local-cache-unpinned`, which is intentionally visible in run provenance.

The extraction API exposes:

- `GET /api/v1/western-blot-extraction-implementations`
- `POST /api/v1/western-blot-extractions`
- `POST /api/v1/western-blot-extraction-invocations/{invocation_id}/replays`

Upload and publish a PDF or image through the artifact API first. Then start a synchronous run:

```bash
curl -X POST http://127.0.0.1:8080/api/v1/western-blot-extractions \
  -H 'Content-Type: application/json' \
  -d '{
    "schema_version": "1.0",
    "source_artifact_id": "REPLACE_WITH_ARTIFACT_UUID",
    "implementation_name": "legacy-local-vlm",
    "dpi": 350,
    "minimum_candidate_score": 0.35,
    "minimum_model_score": 0.65,
    "maximum_candidates": 50,
    "image_max_side": 1800,
    "model_max_tokens": 4096
  }'
```

The response names the stable evaluation case, run, all three invocations, prediction,
publication, trace, pipeline version, and reviewable result counts. Inspect the case through
`/workbench/<case-id>`, `/annotate/<case-id>`, and `/spatial/<case-id>`.

Replay one invocation without replacing it:

```bash
curl -X POST \
  http://127.0.0.1:8080/api/v1/western-blot-extraction-invocations/REPLACE_WITH_INVOCATION_UUID/replays \
  -H 'Content-Type: application/json' \
  -d '{"schema_version":"1.0"}'
```

An assembly replay appends a new prediction and publication. An upstream replay is deliberately
non-cascading and records only that component result.

## Verification

```bash
uv run pytest -p no:cacheprovider \
  tests/baseline/test_hackathon_extraction_baseline.py \
  tests/unit/contracts/test_western_blot_extraction_contracts.py \
  tests/unit/extraction \
  tests/integration/test_extraction_api.py

HIVEBLOT_RUN_LIVE_EXTRACTION=1 \
  uv run pytest -p no:cacheprovider tests/integration/test_extraction_live.py
```

The opt-in acceptance test uses real PostgreSQL and MinIO with the captured Oduah model response.
It verifies immutable source access, full pipeline and evaluation persistence, reruns, assembly
replay, fresh-repository reconstruction, and survival of a human annotation revision. It does not
call an external or GPU model in CI.
