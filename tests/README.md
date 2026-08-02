# Test layout

- `baseline/`: captured behavior from the hackathon extraction boundary.
- `unit/`: strict contracts, configuration, adapters, and deterministic logic.
- `integration/`: application/process-boundary smoke checks without external services.
- root `test_*.py`: retained prototype tests, to be migrated when their owning PRD changes code.
- `fixtures/`: committed, deterministic inputs and expected outputs.

Run every category with `make test`.
