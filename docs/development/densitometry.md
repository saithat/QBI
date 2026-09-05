# Densitometry development

Run the deterministic unit, contract, and synthetic-reference suite without services:

```bash
uv run pytest -p no:cacheprovider \
  tests/unit/contracts/test_densitometry_contracts.py \
  tests/unit/densitometry
```

The 40×24 synthetic blot fixture has two lanes and two targets. Its committed reference values test
raw intensity, background subtraction, loading-control normalization, exact replay, and a one-pixel
geometry perturbation. No model, network, database, or object store is required for this suite.

For real persistence, start PostgreSQL and MinIO and run the opt-in acceptance test:

```bash
docker compose up -d postgres minio
HIVEBLOT_RUN_LIVE_DENSITOMETRY=1 \
  uv run --env-file .env pytest -p no:cacheprovider tests/integration/test_densitometry_live.py
```

The live test applies migration `0010_tool_output_artifacts`, uploads a raster through signed MinIO
access, saves reviewer geometry in PostgreSQL, publishes exact measurements and an immutable overlay,
replays the component, reconstructs services from fresh repositories, and verifies persisted values.

## Review workflow

1. Open `/spatial/<case-id>` and save complete lanes, protein rows, bands, and a loading-control
   relationship in source-image coordinates.
2. Open `/densitometry/<case-id>` from the evidence or spatial workbench.
3. Choose an exact geometry revision and source image. Publication versus raw-source classification
   is displayed before execution.
4. Configure background and normalization, state whether lane boundaries and exposure were reviewed,
   and run the deterministic tool.
5. Inspect corrected and normalized values, suitability, QC warnings, source/configuration hashes,
   overlay, and attempt history.
6. Replay exact inputs, or return to the spatial editor, save a new geometry revision, and run only
   densitometry again.

API routes:

- `GET /api/v1/evaluation-cases/{case_id}/densitometry-workbench`
- `POST /api/v1/evaluation-cases/{case_id}/densitometry-runs`
- `GET /api/v1/densitometry-invocations/{invocation_id}`
- `POST /api/v1/densitometry-invocations/{invocation_id}/replays`

All request bodies carry `schema_version: "1.0"` and reject unknown fields. Large image and overlay
bytes use expiring signed object-store URLs rather than FastAPI proxying.
