# Test layout

- `baseline/`: captured behavior from the hackathon extraction boundary.
- `unit/`: strict contracts, configuration, adapters, and deterministic logic.
- `integration/`: application/process-boundary smoke checks; live PostgreSQL/MinIO coverage is
  opt-in with `HIVEBLOT_RUN_LIVE_STORAGE=1`, `HIVEBLOT_RUN_LIVE_EVALUATION=1`, or
  `HIVEBLOT_RUN_LIVE_REVIEW_QUEUE=1`. The queue suite seeds and removes 10,000 cases.
- root `test_*.py`: retained prototype tests, to be migrated when their owning PRD changes code.
- `fixtures/`: committed, deterministic inputs and expected outputs.

Run every category with `make test`.
