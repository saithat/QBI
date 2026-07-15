# HiveBlot Local

HiveBlot extracts western blot evidence from scientific PDFs and makes the resulting
band-level records searchable. The complete stack runs locally: PostgreSQL stores records,
Qwen3-VL runs through vLLM on an NVIDIA GPU, and FastAPI serves both the API and a minimal
viewer.

## What runs

```text
PDF -> page rendering and CV crop selection -> Qwen3-VL extraction
    -> validated band records -> PostgreSQL -> FastAPI viewer/search
```

The repository has no cloud-service dependency and requires no external service credentials.

## Requirements

- Linux with an NVIDIA GPU and a working `nvidia-smi`
- Docker Engine, Docker Compose, and NVIDIA Container Toolkit/CDI
- `Qwen/Qwen3-VL-8B-Instruct` in the host Hugging Face cache
- About 17 GiB of VRAM for the model; the checked-in defaults target a 24 GiB RTX 3090

The tested host cache is `/home/saithat/.cache/huggingface`. Set `HF_CACHE_DIR` in `.env`
if yours is elsewhere.

## Start the stack

```bash
cp .env.example .env
make up
docker compose ps
```

The first vLLM start takes roughly a minute to load and profile the model. Once the services
are healthy, open <http://localhost:8080>. The database is initially empty.

Useful diagnostics:

```bash
make logs
curl http://localhost:8080/health
curl http://localhost:8000/v1/models
```

## Ingest a PDF

Place a paper under `data/input/`, then run:

```bash
make ingest PDF=paper.pdf
```

The pipeline:

1. Extracts a DOI when available and renders the PDF.
2. Scores candidate western blot crops with computer vision.
3. Sends qualifying crops and nearby paper text to local Qwen3-VL.
4. Validates structured output and preserves present, absent, and uncertain bands.
5. Upserts band-level rows into PostgreSQL.
6. Writes resumable manifests under `data/runs/<paper-id>/`.

Successful VLM results are cached. Re-running the same command queries only missing or failed
candidates and safely upserts all cached records. Use the lower-level command with
`--no-cache` only when a complete re-extraction is intentional:

```bash
docker compose run --rm app python -m hiveblot.pipeline /data/input/paper.pdf --no-cache
```

## API

- `GET /health` checks PostgreSQL and vLLM readiness.
- `GET /api/records` accepts `target`, `sample`, `condition`, `limit`, and `offset`.
- `POST /api/search` accepts a natural-language query and uses Qwen to extract safe filters.

Example:

```bash
curl -X POST http://localhost:8080/api/search \
  -H 'Content-Type: application/json' \
  -d '{"query":"p53 in A549 cells treated with Nutlin-3","limit":50}'
```

The model never generates executable SQL. It returns a JSON object containing `target`,
`sample`, and `condition`; the application maps those values into parameterized queries.

## Development

```bash
make lint
make test
docker compose config
```

Generated PDFs, images, run output, databases, model weights, and `.env` files are ignored by
Git. Stop services with `make down`; add `-v` to `docker compose down` only when you explicitly
want to delete the local PostgreSQL volume.

## Current constraints

- vLLM is configured for one concurrent request so the BF16 model fits comfortably on 24 GiB.
- Viewer searches queue behind active PDF extraction requests on the single GPU.
- Ingestion is a CLI workflow; the viewer intentionally does not accept file uploads.
- Extraction quality still needs scientific review against the source image and paper.
