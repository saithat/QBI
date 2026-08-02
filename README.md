# HiveBlot

HiveBlot turns western blot evidence from scientific papers into structured, searchable,
reviewable observations. This repository is currently at **PRD-001: platform foundation**.
The useful local hackathon extraction path remains operational while strict contracts,
configuration, test fixtures, and repository boundaries are established around it.

## Current data flow

```text
PDF -> page rendering -> CV crop selection -> local Qwen3-VL extraction
    -> legacy normalization -> PostgreSQL -> FastAPI -> local evidence viewer
```

PRD-001 does not add artifact upload, S3/MinIO, evaluation UI, Kubernetes, crawling,
densitometry, authentication, or distributed execution.

## Repository boundaries

```text
apps/api/                  FastAPI entry point and HTTP-only schemas
workers/extraction/        stable worker entry point around retained extraction code
packages/contracts/        strict shared Pydantic v2 contracts and JSON Schemas
hiveblot/                  retained domain, persistence, model, and extraction modules
services/                  future long-lived service boundary (no PRD-001 service)
infra/                     deployment documentation; root Compose files stay compatible
tests/baseline/            historic extraction behavior without GPU/network/database
tests/unit/                contract and configuration tests
tests/integration/         boundary smoke tests
docs/                      audit, ADR, architecture, development, and PR notes
```

The canonical ASGI import is `apps.api.main:app`. The canonical ingestion executable is
`hiveblot-ingest`; the old `hiveblot.api:app` import remains as a compatibility facade.

## Fast development setup

The quality suite needs Python 3.12 and [uv](https://docs.astral.sh/uv/), but no GPU or
running services:

```bash
make setup
make check
```

`make check` verifies formatting, lint, static types, JSON Schema snapshots, the API smoke
boundary, and all tests. Regenerate schemas intentionally with `make schemas`.

## Run the complete local prototype

Requirements:

- Linux with an NVIDIA GPU and working `nvidia-smi`
- Docker Engine, Docker Compose, and NVIDIA Container Toolkit/CDI
- `Qwen/Qwen3-VL-8B-Instruct` present in a host Hugging Face cache
- About 17 GiB VRAM; defaults target a 24 GiB GPU

```bash
cp .env.example .env
# Set HF_CACHE_DIR in .env to the real host cache path.
make up
docker compose ps
curl http://localhost:8080/health
```

Open <http://localhost:8080> after the services become healthy.

## Ingest a PDF

Place a paper under `data/input/`, then run:

```bash
make ingest PDF=paper.pdf
```

The retained pipeline extracts a DOI, renders pages, identifies candidate crops, calls the
local model with schema-constrained output, preserves present/absent/uncertain bands, upserts
records, and writes resumable manifests under `data/runs/<paper-id>/`. Reuse of successful
model output remains enabled by default.

## API

- `GET /health` checks PostgreSQL and vLLM readiness.
- `GET /api/records` accepts `target`, `sample`, `condition`, `limit`, and `offset`.
- `GET /api/records/{id}` exposes one record with locally available source context.
- `POST /api/search` accepts a natural-language query and maps model output to safe filters.

Public JSON bodies now carry `schema_version: "1.0"` and reject unknown request fields. The
model never generates executable SQL; domain criteria are mapped to parameterized queries.

## Preserved baseline

The committed Oduah 2024 fixture captures one real historic model response and all 40 records
produced by its legacy normalizer. Source bytes are not committed; their SHA-256 values are in
`tests/fixtures/baseline/manifest.json`. This makes useful behavior reproducible in CI without
a GPU, source publication, model service, or database.

See [the repository audit](docs/architecture/repository-audit.md),
[the foundation ADR](docs/adr/0001-platform-foundation.md), and
[development setup](docs/development/setup.md) for details.
