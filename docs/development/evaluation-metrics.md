# Evaluation metrics development

Start PostgreSQL and the API:

```bash
docker compose up -d postgres
uv run uvicorn apps.api.main:app --host 127.0.0.1 --port 8080
```

Open <http://127.0.0.1:8080/metrics> after creating at least two metric runs. The dashboard keeps
selected baseline/candidate IDs and history filters in the URL. It shows separate stage scores,
group slices, reliability buckets, Brier score, expected calibration error, precision/risk versus
coverage, and workbench links for individual regressions and improvements.

The API exposes:

- `POST|GET /api/v1/evaluation-metric-runs`
- `GET /api/v1/evaluation-metric-runs/{metric_run_id}`
- `GET /api/v1/evaluation-metric-runs/{metric_run_id}/cases/{case_id}`
- `GET /api/v1/evaluation-metric-runs/{metric_run_id}/calibration?category=field`
- `POST /api/v1/evaluation-pipeline-comparisons`

Create a canonical `EvaluationScoringInput` JSON document and run the reproducible CLI with:

```bash
uv run hiveblot-evaluate scoring-input.json --output metric-run.json
uv run hiveblot-evaluate scoring-input.json --output second-run.json
cmp metric-run.json second-run.json
```

The input must include a valid dataset content hash and exactly one submitted prediction for every
frozen reference case. Unknown fields, string-to-number coercion, duplicate observation keys,
non-finite confidence, unordered thresholds, and mismatched case sets fail validation.

Verification commands:

```bash
uv run pytest tests/unit/contracts/test_metric_contracts.py \
  tests/unit/evaluation/test_metrics.py \
  tests/unit/evaluation/test_metrics_cli.py \
  tests/integration/test_metrics_api.py
node --check apps/web/assets/metrics.js
HIVEBLOT_RUN_LIVE_METRICS=1 \
  uv run pytest tests/integration/test_metrics_live.py
```

The live test applies migration `0008`, persists baseline and candidate runs, reconstructs a run
through a fresh repository, filters history, slices field calibration, compares pipelines, and
removes its test rows. It does not need MinIO because metric runs contain immutable artifact UUID
references rather than file bytes.
