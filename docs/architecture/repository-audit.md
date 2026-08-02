# Existing repository audit

Audit date: 2026-08-02. Baseline revision: `8b38694` plus preserved local prototype work.

## Applications and entry points

The repository contained one Python distribution and one deployable application:

- `hiveblot.api:app`: FastAPI application serving JSON routes and a single-file static viewer.
- `hiveblot.pipeline:main`: PDF ingestion CLI, registered as `hiveblot-ingest`.
- `uvicorn hiveblot.api:app`: Docker container command.
- `make ingest PDF=<name>`: Compose wrapper around the ingestion module.

PRD-001 establishes `apps.api.main:app` and `workers.extraction.entrypoint:main` as canonical
paths while retaining compatibility imports.

## Extraction workflow

`hiveblot.pipeline.run_pdf_pipeline` orchestrated the complete workflow synchronously:

1. `pdf_preprocess.extract_paper_doi` identifies a paper.
2. PyMuPDF renders pages and extracts text.
3. OpenCV generates and scores candidate crops.
4. Candidate-specific nearby text and caption/method snippets are assembled.
5. `OpenAICompatibleVLM` calls an OpenAI-compatible local vLLM endpoint.
6. Successful JSON output is cached in JSON/JSONL files.
7. `records.flatten_records` expands figure/panel output into band rows.
8. `db.upsert_records` writes idempotent rows to PostgreSQL.

The flow is useful but not yet a versioned component graph. It mixes orchestration, local file
manifests, raw dictionaries, model calls, and persistence callbacks. Moving it into pipeline
run/provenance contracts is PRD-011, not PRD-001.

## Model integrations and prompts

Two local model calls existed:

- Western blot extraction in `hiveblot.vlm_extract`, using `PROMPT`, a hand-written JSON Schema,
  temperature 0, image/text input, and Qwen thinking disabled.
- Search-filter parsing in `hiveblot.model_client`, using `SEARCH_SYSTEM_PROMPT`, a hand-written
  three-field JSON Schema, and parameterized downstream SQL.

Useful prompts and model request behavior are retained. Historic extraction output is captured
as a baseline. Raw scientific model output is not yet registered with invocation/cost/trace
metadata; that remains a documented limitation until pipeline registry work.

## Representations and schemas before migration

| Boundary | Previous representation | Risk |
|---|---|---|
| API search request | local Pydantic model, `extra=forbid` | no explicit version; responses were dictionaries |
| Search model output | Pydantic `SearchFilters` | also passed directly into persistence querying |
| Extraction model output | nested dictionaries + manual JSON Schema | permissive normalization; no canonical version |
| Pipeline summaries | dictionaries serialized to JSON | no stable public schema |
| Database writes/reads | dictionaries from raw SQL | indistinguishable by type from API/model payloads |
| Settings | frozen dataclass + `os.getenv`/dotenv | coercion and deployed defaults were not validated |
| Queue/job messages | none | no shared execution contract existed |
| SQLAlchemy entities | none; psycopg and SQL are used directly | future ORM types must remain persistence-only |

PRD-001 separates HTTP Pydantic models, model predictions, domain criteria, persistence
TypedDicts, and queue contracts. The shared canonical contract package has no application,
worker, database, or model-client dependency.

## File and database storage

- Input PDFs: ignored `data/input/` files.
- Page renders, crops, contexts, raw/cached model results, and summaries: ignored
  `data/runs/<paper-id>/` files.
- Searchable band rows: PostgreSQL table `western_blot_records` created from `schema.sql`.
- Static UI: `hiveblot/static/index.html` served directly by FastAPI.

The current local filesystem is mutable and path-addressed. No S3-compatible artifact store,
content-addressed publication, signed URL, or upload state exists; all belong to PRD-002.

## Configuration and secrets

Configuration previously used direct environment reads with local defaults. The tracked files
contained local placeholder credentials only; a tracked-file pattern scan found no private key
or external credential. `.env`, generated data, model caches, and virtual environments are
ignored.

PRD-001 replaces configuration parsing with Pydantic Settings, adds explicit local/test/deployed
profiles, masks the model API key, rejects missing or unsafe deployed values, removes the
machine-specific default Hugging Face cache path, and installs locked dependencies in Docker.

## Tests and baseline behavior

Before restructuring:

- 21 tests passed in 0.29 seconds.
- Ruff lint passed.
- `docker compose config --quiet` passed.
- The running PostgreSQL container was healthy; the API container was running but unhealthy
  because its required vLLM service was absent.
- `GET /api/records?limit=1` returned a stored Oduah 2024 record successfully.

The ignored representative run for DOI `10.3389/fonc.2024.1363543` contained 9 pages, 62 CV
candidates, 3 model candidates, 2 positive candidates, and 80 database rows. Page 4 produced 40
rows and is now the committed GPU-free baseline fixture. Source PDF/crop bytes remain ignored;
their hashes are recorded in the fixture manifest.

## Deployment files and setup issues

The repository contained a root Dockerfile and Compose stack for PostgreSQL, vLLM, and the app.
There was no CI, Kubernetes, Terraform, migration framework, or production secret integration.
Full local execution requires Docker socket access, NVIDIA runtime support, a populated model
cache, and enough GPU memory. API readiness intentionally fails when vLLM is unavailable even
though database-only record routes may still work.

## Experimental and preserved code

The static source-detail viewer, context extraction helpers, model thinking controls, broader
search token handling, cache/retry logic, prompts, CV preprocessing, SQL schema, and extraction
normalization are all retained. None is presented as adjudicated scientific output.
