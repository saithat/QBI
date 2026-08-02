.PHONY: up down logs setup format lint typecheck schemas schema-check test check docker-test ingest

up:
	docker compose up --build -d

down:
	docker compose down

logs:
	docker compose logs -f app minio vllm

setup:
	uv sync --locked --all-extras

format:
	uv run ruff format .
	uv run ruff check --fix .

lint:
	uv run ruff format --check .
	uv run ruff check --no-cache .

typecheck:
	uv run mypy apps hiveblot packages/contracts/src/hiveblot_contracts \
		packages/storage/src/hiveblot_storage workers

schemas:
	uv run hiveblot-schemas

schema-check:
	uv run hiveblot-schemas --check

test:
	uv run pytest -p no:cacheprovider

check: lint typecheck schema-check test

docker-test:
	docker compose run --rm --no-deps app pytest -p no:cacheprovider

ingest:
	@test -n "$(PDF)" || (echo "Usage: make ingest PDF=paper.pdf" && exit 2)
	@test -f "data/input/$(PDF)" || (echo "Missing data/input/$(PDF)" && exit 2)
	docker compose run --rm app python -m workers.extraction.entrypoint "/data/input/$(PDF)"
