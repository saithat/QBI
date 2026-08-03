# HiveBlot

HiveBlot turns western blot evidence from scientific papers into structured, searchable,
reviewable observations. Milestones A through E and organization isolation from Milestone F are
implemented.
The evaluation UI includes a server-paginated review queue, source evidence workbench, typed
western-blot annotation editor, source-pixel spatial review, and deterministic densitometry. The
useful local hackathon extraction path remains operational while new scientific state enters through
strict, versioned contracts and finite work can run through durable local Docker or Kubernetes
jobs.

## Current data flow

```text
PDF -> page rendering -> CV crop selection -> local Qwen3-VL extraction
    -> legacy normalization -> PostgreSQL -> FastAPI -> local evidence viewer

user multipart upload / allowlisted source adapter -> S3 staging -> hash + MIME validation
    -> content-addressed S3 object + PostgreSQL metadata/events -> expiring download URL

immutable artifact -> evaluation case -> immutable predictions + independent reviewer revisions
    -> explicit adjudication references

evaluation cases -> server-side review filters -> paginated browser -> optimistic assignment

selected case -> artifact metadata + source context + immutable revision overlays
    -> browser streams selected bytes from an expiring object-store URL

structured prediction -> typed western-blot review -> optimistic autosave revisions
    -> semantic diff + append-only undo + canonical entity references

prediction/source regions -> source-pixel graph editor -> validated spatial relationships
    -> semantic geometry diff + immutable reviewer revisions

pipeline DAG -> parent-linked component invocations -> strict output validation
    -> immutable failure/replay history -> explicit producer-linked publication

frozen references + versioned pipeline submission -> deterministic stage/end-to-end scoring
    -> calibration + grouped slices -> same-snapshot regression comparison

reviewed cases -> versioned golden-dataset draft -> leakage-safe promotion
    -> content-addressed snapshot -> deterministic JSONL + manifest exports

published PDF/image artifact -> verified bytes -> detection -> versioned model normalization
    -> structured/spatial case assembly -> immutable prediction + publication

immutable raster + exact geometry revision -> deterministic pixel measurement + QC
    -> content-addressed overlay -> immutable result/publication + exact replay

strict finite job + immutable inputs -> durable PostgreSQL lease + heartbeat
    -> restricted local container -> queryable logs + provenance-linked immutable outputs

strict finite job + immutable inputs -> Kubernetes scheduler worker -> bounded Job
    -> queryable logs + the same durable provenance-linked output publication

official source API -> immutable raw response + strict versioned discovery batch/evidence
    -> canonical URL/accession dedupe -> durable prioritized crawl frontier

eligible frontier -> durable fetch lease + shared per-domain permit + robots/HTTP policy
    -> immutable raw artifact -> acquisition provenance + downstream parse outbox

opaque bearer token -> authenticated user + active organization memberships
    -> backend scope decision + append-only audit -> tenant-filtered scientific state
```

Kubernetes is the deployed placement layer rather than the state store; PostgreSQL, object storage,
and Temporal stay externally managed. The retained
extractor runs through stored artifacts and versioned scientific stages; the filesystem ingestion
CLI remains available as a compatibility path.

## Repository boundaries

```text
apps/api/                  FastAPI entry point and HTTP-only schemas
apps/web/                  dependency-free review, annotation, metrics, and densitometry surfaces
workers/extraction/        stable worker entry point around retained extraction code
packages/contracts/        strict shared Pydantic v2 contracts and JSON Schemas
packages/auth/             opaque-token identity, organization RBAC, scope policy, and auditing
packages/evaluation/       cases, predictions, reviews, assignments, and adjudication
packages/extraction/       versioned western-blot normalization and pipeline orchestration
packages/densitometry/     deterministic pixel measurement, QC, and provenance orchestration
packages/storage/          immutable publication, S3, and PostgreSQL storage boundaries
services/job-service/      domain-independent jobs, leases, attempts, logs, and execution
services/crawler/          discovery, durable fetch queue, HTTP policy, and shared domain limits
workers/jobs/              bounded operations plus local-Docker/Kubernetes worker entry points
workers/discovery/         bounded official-source discovery CronJob/CLI entry point
workers/fetching/          long-lived queue-backed public-source fetch worker
workers/workflow/          external Temporal workflow-worker entry point
hiveblot/                  retained domain, persistence, model, and extraction modules
services/                  future long-lived service boundary
infra/kubernetes/          Kustomize base, kind overlay, and runtime acceptance smoke
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

Local/test configuration explicitly defaults to disabled authentication. Deployed configuration
requires bearer authentication. See [authorization development](docs/development/authorization.md)
before enabling bearer mode or bootstrapping users.

## Run the complete local prototype

Requirements:

- Linux with an NVIDIA GPU and working `nvidia-smi`
- Docker Engine, Docker Compose, and NVIDIA Container Toolkit/CDI
- `Qwen/Qwen3-VL-8B-Instruct` present in a host Hugging Face cache
- About 17 GiB VRAM; defaults target a 24 GiB GPU

```bash
make local-env
# Set HF_CACHE_DIR in .env to the real host cache path.
make up
docker compose ps
curl http://localhost:8080/health
```

Open <http://localhost:8080> for the retained evidence index or <http://localhost:8080/review> for
the evaluation review queue after the services become healthy. Cases link to `/workbench/{id}` for
source inspection, `/annotate/{id}` for structured review, and `/spatial/{id}` for geometry review.
Use `/densitometry/{id}` to measure an exact geometry revision, inspect QC and reproducibility, and
replay the deterministic component.

Open <http://localhost:8080/metrics> to inspect versioned metric runs, confidence calibration,
grouped performance, and pipeline regressions.

To run only durable storage dependencies without a GPU:

```bash
docker compose up --build -d postgres minio
```

MinIO exposes its S3 API at <http://localhost:9000> and local console at
<http://localhost:9001>. See [artifact storage setup](docs/development/artifact-storage.md).

## Run the local Kubernetes runtime

With Docker, kind, and kubectl installed:

```bash
make local-env
make kind-up
make kind-smoke
make kind-down
```

This keeps PostgreSQL and MinIO in Compose, runs application/worker Deployments in kind, and proves
that a strict generic job can execute as a Kubernetes Job and publish the preserved 40-record output.
The kind web/API endpoint is <http://127.0.0.1:18080>. See
[Kubernetes development](docs/development/kubernetes.md).

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

- `GET /health` checks PostgreSQL and vLLM readiness; `/health/startup`, `/health/live`, and
  `/health/ready` expose Kubernetes-specific process and readiness checks.
- `GET /api/v1/auth/me` returns the authenticated principal and active organization memberships.
- Organization administration and audit routes manage scoped memberships and inspect append-only
  allow/deny events.
- `GET /api/records` accepts `target`, `sample`, `condition`, `limit`, and `offset`.
- `GET /api/records/{id}` exposes one record with locally available source context.
- `POST /api/search` accepts a natural-language query and maps model output to safe filters.
- `POST /api/v1/artifact-uploads` creates an expiring direct multipart upload.
- `POST /api/v1/artifact-uploads/{id}/complete` validates and publishes staged bytes.
- `GET /api/v1/artifacts/{id}` returns metadata without downloading bytes.
- `POST /api/v1/artifacts/{id}/download-url` records access and returns an expiring S3 URL.
- `POST /api/v1/artifacts/source-ingestions` uses an explicitly allowlisted source adapter.
- `POST /api/v1/evaluation-cases` anchors review state to immutable artifacts.
- Case prediction routes preserve raw and normalized outputs as distinct immutable documents.
- Annotation revision routes append snapshots and return `409` for stale expected-head UUIDs.
- `GET /api/v1/review-queue` provides server-side filters and bounded pagination.
- `/api/v1/review-views` persists owner-scoped, optimistic-versioned filter views.
- `GET /api/v1/evaluation-cases/{id}/workbench` aggregates source metadata and immutable overlays.
- Source-context routes append caption and nearby-text revisions to exact case/artifact/role
  associations, returning `409` when an expected head is stale.
- `GET /api/v1/evaluation-cases/{id}/structured-editor` aggregates predictions, review history,
  semantic diffs, and error codes for one reviewer.
- `PUT /api/v1/evaluation-cases/{id}/structured-annotations` appends a validated autosave revision.
- Prediction-acceptance and structured-undo routes produce drafts and new revisions without
  replacing prediction or annotation history.
- `GET /api/v1/canonical-entities` provides bounded canonical entity suggestions.
- `GET /api/v1/evaluation-cases/{id}/spatial-editor` composes spatial predictions, the reviewer
  head, revision summaries, error codes, and a stable-ID geometry diff.
- Spatial save, prediction-acceptance, and restore routes validate complete source-pixel graphs and
  append revisions without reverting current non-spatial annotations.
- Pipeline definition/run routes register versioned component graphs and expose immutable invocation
  history per evaluation case.
- Component result routes preserve raw and normalized output separately; selective replay appends a
  new attempt, and publication validates every final value against its producing invocation.
- Evaluation metric routes score frozen reference/submission pairs, filter immutable run history,
  expose case/category calibration detail, and compare two pipeline versions on the same snapshot.
- `hiveblot-evaluate` reproduces a canonical metric-run document from a strict scoring-input file.
- Golden-dataset routes manage optimistic drafts, exact-revision promotion, paper/content leakage
  checks, immutable snapshots, transition history, and content-addressed JSONL/manifest exports.
- Western-blot extraction routes list pinned implementations, run stored PDF/image artifacts
  through detection/model/assembly stages, and selectively replay immutable component attempts.
- Densitometry routes list complete geometry revisions, run exact deterministic measurements,
  expose signed source/overlay access, and append exact-input replay attempts.
- Generic job routes submit strict finite workloads, inspect status and attempt history, cancel work,
  and query bounded stdout/stderr/system logs.
- Discovery batch/frontier routes preserve source observations, deduplicate canonical URLs and
  accessions, filter/prioritize pending work, enforce optimistic scheduling, and link acquired
  immutable artifacts.
- Crawl fetch routes expose durable task/attempt provenance and queue, worker, retry, latency,
  status, byte, source-error, and deduplication metrics.
- `hiveblot-discover-pmc` performs bounded API-first discovery through PMC's official OAI endpoint.
- `hiveblot-fetch-worker` leases and acquires approved sources without multiplying per-domain limits.

Public JSON bodies now carry `schema_version: "1.0"` and reject unknown request fields. The
model never generates executable SQL; domain criteria are mapped to parameterized queries.

## Preserved baseline

The committed Oduah 2024 fixture captures one real historic model response and all 40 records
produced by its legacy normalizer. Source bytes are not committed; their SHA-256 values are in
`tests/fixtures/baseline/manifest.json`. This makes useful behavior reproducible in CI without
a GPU, source publication, model service, or database.

See [the repository audit](docs/architecture/repository-audit.md),
[the foundation ADR](docs/adr/0001-platform-foundation.md), and
[the artifact storage ADR](docs/adr/0002-content-addressed-artifact-storage.md), and
[the structured annotation ADR](docs/adr/0006-structured-annotation-revisions.md), and
[the spatial review ADR](docs/adr/0007-source-pixel-spatial-review.md), and
[the pipeline provenance ADR](docs/adr/0008-immutable-pipeline-runs-and-replay.md), and
[the evaluation metrics ADR](docs/adr/0009-versioned-evaluation-metrics.md), and
[the golden dataset ADR](docs/adr/0010-content-addressed-golden-datasets.md), and
[the western-blot extraction ADR](docs/adr/0011-versioned-western-blot-extraction.md), and
[the deterministic densitometry ADR](docs/adr/0012-deterministic-densitometry.md), and
[the generic job-service ADR](docs/adr/0013-durable-generic-job-service.md), and
[the Kubernetes runtime ADR](docs/adr/0014-kubernetes-runtime.md), and
[the public discovery ADR](docs/adr/0015-api-first-public-discovery.md), and
[the distributed fetching ADR](docs/adr/0016-postgres-fetch-queue-and-shared-domain-permits.md), and
[development setup](docs/development/setup.md) for details.
