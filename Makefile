.PHONY: up down logs lint test ingest

up:
	docker compose up --build -d

down:
	docker compose down

logs:
	docker compose logs -f app vllm

lint:
	docker compose run --rm --no-deps app ruff check --no-cache .

test:
	docker compose run --rm --no-deps app pytest -p no:cacheprovider

ingest:
	@test -n "$(PDF)" || (echo "Usage: make ingest PDF=paper.pdf" && exit 2)
	@test -f "data/input/$(PDF)" || (echo "Missing data/input/$(PDF)" && exit 2)
	docker compose run --rm app python -m hiveblot.pipeline "/data/input/$(PDF)"
