# Test layout

- `baseline/`: captured behavior from the hackathon extraction boundary.
- `unit/`: scientific calculations, custom domain invariants, immutable revisions, tenant
  boundaries, job execution, source-fetch policy, and configuration/packaging regressions.
- `integration/`: selected HTTP permission, citation, conflict, and route-wiring checks, a shared
  fetch-rate check, and opt-in acceptance tests against real PostgreSQL/MinIO storage.
- root `test_*.py`: retained extraction, model, image-processing, and query-safety regressions.
- `fixtures/`: committed, deterministic inputs and expected outputs.

Run the default suite with `make test`; live checks are skipped unless enabled explicitly.
`make check` also runs formatting, lint, static types, and JSON Schema snapshot verification.
With Docker running, `make docker-test` builds the application image and runs the default suite
inside it. This target needs no `.env`, GPU, or running application services.

Keep tests focused on observable behavior and failures. Framework serialization checks,
mocked repository CRUD, and per-feature HTTP walkthroughs duplicate other coverage and should
not be added by default. The dedicated schema check verifies the complete snapshot set.

## Live service checks

Set the relevant flag to `1` and run its test file. The live fixtures generally disable automatic
`.env` loading, so pass credentials through the environment or use `uv run --env-file .env`.
See [development setup](../docs/development/setup.md) for local dependency and test commands.

| Coverage | Enable flag | Test file under `integration/` |
| --- | --- | --- |
| Artifact storage | `HIVEBLOT_RUN_LIVE_STORAGE` | `test_artifact_storage_live.py` |
| Evaluation storage | `HIVEBLOT_RUN_LIVE_EVALUATION` | `test_evaluation_storage_live.py` |
| Paginated review queue | `HIVEBLOT_RUN_LIVE_REVIEW_QUEUE` | `test_review_queue_live.py` |
| Source workbench | `HIVEBLOT_RUN_LIVE_WORKBENCH` | `test_workbench_live.py` |
| Structured annotations | `HIVEBLOT_RUN_LIVE_STRUCTURED_EDITOR` | `test_structured_editor_live.py` |
| Spatial annotations | `HIVEBLOT_RUN_LIVE_SPATIAL_EDITOR` | `test_spatial_editor_live.py` |
| Pipeline registry | `HIVEBLOT_RUN_LIVE_PIPELINE` | `test_pipeline_registry_live.py` |
| Evaluation metrics | `HIVEBLOT_RUN_LIVE_METRICS` | `test_metrics_live.py` |
| Golden datasets | `HIVEBLOT_RUN_LIVE_GOLDEN` | `test_golden_live.py` |
| Extraction | `HIVEBLOT_RUN_LIVE_EXTRACTION` | `test_extraction_live.py` |
| Densitometry | `HIVEBLOT_RUN_LIVE_DENSITOMETRY` | `test_densitometry_live.py` |
| Durable jobs | `HIVEBLOT_RUN_LIVE_JOBS` | `test_job_service_live.py` |
| Discovery | `HIVEBLOT_RUN_LIVE_DISCOVERY` | `test_discovery_live.py` |
| Distributed fetching | `HIVEBLOT_RUN_LIVE_FETCH` | `test_fetching_live.py` |
| Authorization | `HIVEBLOT_RUN_LIVE_AUTH` | `test_authorization_live.py` |
| Evidence search | `HIVEBLOT_RUN_LIVE_SEARCH` | `test_search_live.py` |

Authorization and evidence-search acceptance tests require isolated databases; follow their
[authorization](../docs/development/authorization.md) and
[search](../docs/development/evidence-search.md) setup instructions.

For actual container execution, also set `HIVEBLOT_RUN_DOCKER_JOBS=1` after building the image
documented in [job-service setup](../docs/development/job-service.md).
