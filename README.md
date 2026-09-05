# HiveBlot

Extract, review, and search western blot evidence from scientific papers.

## Development

Use Python 3.12 and uv:

```sh
make setup
make check
```

`make check` runs formatting, lint, types, schema snapshots, and default tests without external
services. `make schemas` regenerates contracts; `make docker-test` runs tests in a Docker image.

## Local API and review UI

```sh
make local-env
mkdir -p data/input data/runs
docker compose up --build -d --wait postgres minio
uv run uvicorn apps.api.main:app --host 127.0.0.1 --port 8080
```

The environment generator creates private credentials and refuses to overwrite `.env`.
API startup applies pending database migrations. Open [the review UI](http://localhost:8080/review)
or [the API reference](http://localhost:8080/docs). This setup needs no GPU; model-backed
extraction and legacy `/api/search` additionally need vLLM.

## PDF extraction

The full Compose stack requires Linux, an NVIDIA GPU with Docker GPU support, and a locally cached
`Qwen/Qwen3-VL-8B-Instruct` model. Set `HF_CACHE_DIR` in `.env`; Compose loads the model offline.
After the setup above:

```sh
make up
# Place paper.pdf under data/input/.
make ingest PDF=paper.pdf
```

The full stack serves the API on port 8080; stop any host API process before starting it.
Inputs and resumable outputs under `data/` are ignored by Git. `make down` stops Compose services.

## Live checks

Live test files under `tests/integration/` specify their opt-in environment flags. For example,
with PostgreSQL and MinIO running:

```sh
HIVEBLOT_RUN_LIVE_STORAGE=1 uv run --env-file .env pytest tests/integration/test_artifact_storage_live.py
```

Pass credentials explicitly; live fixtures disable automatic dotenv loading. Authorization and
search acceptance tests require separate disposable databases.

## Configuration and entry points

Configuration lives in [.env.example](.env.example), [.env.deployed.example](.env.deployed.example),
and [hiveblot/settings.py](hiveblot/settings.py). Local authentication defaults to disabled;
deployed configuration requires bearer authentication. Use `uv run hiveblot-auth-bootstrap --help`
for user credentials and `uv run hiveblot-auth-platform-token --help` for search administration.

The API starts at [apps/api/main.py](apps/api/main.py); CLI entry points are listed in
[pyproject.toml](pyproject.toml). SQL migrations live under `hiveblot/migrations/`.

Kubernetes manifests and startup scripts live under [infra/kubernetes](infra/kubernetes).
With Docker Compose, kind, kubectl, and ripgrep installed, run `make kind-up`, then `make kind-smoke`.
The local cluster serves port 18080; `make kind-down` removes it. Temporal requires an external
service and enabling its worker replicas.
