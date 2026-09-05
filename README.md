# HiveBlot

A public, searchable database of western blot records extracted from papers and images.
Maintainers import sources; visitors browse proteins, samples, conditions, band states, and
source images with paper citations and extraction confidence.

## Run the catalog

Use Python 3.12, Node.js 24, uv, and Docker Compose:

```sh
make setup
make local-env
make up
```

Open [the catalog](http://localhost:8080) or [the API reference](http://localhost:8080/docs).
Browsing runs on CPU with PostgreSQL and the stored images. `make down` stops the services.
`make local-env` generates private database credentials and creates `data/input` and `data/runs`.
It refuses to overwrite an existing `.env`.

## Import sources

Configure the vision endpoint and model in `.env`; [.env.example](.env.example) lists the settings.
For the optional local GPU service, install NVIDIA Docker support, set `HF_CACHE_DIR`
to a pre-downloaded `Qwen/Qwen3-VL-8B-Instruct` cache, and run `make model`.

```sh
uv run hiveblot-ingest data/input/paper.pdf
uv run hiveblot-ingest data/input/blot.png --paper-id my-paper --context "Figure caption"
uv run hiveblot-ingest --help
```

Use `--paper-id` and `--source-url` to supply the paper citation. A stable `--source-id` identifies
one input within a paper; it defaults to the input filename. Re-importing replaces that source's
records only after extraction succeeds. Other images from the same paper remain intact.
Changed source bytes or extraction settings invalidate the cache.
Completed extractions are reused when rerunning an interrupted import. Published images and
metadata are preserved separately from the working cache, so a failed retry cannot alter them.

Keep `data/runs` with the database when moving the catalog: it contains source images, model output,
and paper context. These files and `.env` are ignored by Git. The web service mounts source data
read-only; imports run through the maintainer CLI.

## Development

The React and TypeScript frontend lives in `frontend/src`. `make frontend-dev` starts its
development server at http://localhost:5173 and forwards API requests to the running catalog.
`make frontend-build` checks TypeScript and builds assets for the Python web service. Build the
frontend before `uv build` to include it in the Python distribution; Docker builds it automatically.

```sh
make check
make docker-test
```

CI also exercises source replacement and queries against PostgreSQL. To run that check locally,
set `HIVEBLOT_TEST_DATABASE_URL` to a disposable PostgreSQL database.

The API starts in [hiveblot/api.py](hiveblot/api.py), ingestion in
[hiveblot/pipeline.py](hiveblot/pipeline.py), and storage in [hiveblot/db.py](hiveblot/db.py).
