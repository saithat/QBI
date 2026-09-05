HIVEBLOT_DOCKER_TEST_IMAGE ?= hiveblot:test

.PHONY: setup local-env up down logs model ingest frontend-dev frontend-build format lint typecheck test check docker-test

setup:
	npm --prefix frontend ci
	uv sync --locked --all-extras
	$(MAKE) frontend-build

frontend-dev:
	npm --prefix frontend run dev

frontend-build:
	npm --prefix frontend run build

local-env:
	uv run hiveblot-env

up:
	docker compose up --build -d --wait

down:
	docker compose --profile gpu down

logs:
	docker compose logs -f app postgres

model:
	docker compose --profile gpu up -d vllm

ingest:
	@test -n "$(SOURCE)" || (echo "Usage: make ingest SOURCE=data/input/paper.pdf" && exit 2)
	uv run hiveblot-ingest "$(SOURCE)"

format:
	uv run ruff format .
	uv run ruff check --fix .

lint:
	uv run ruff format --check .
	uv run ruff check --no-cache .

typecheck:
	uv run mypy hiveblot

test: frontend-build
	uv run pytest -p no:cacheprovider

check: lint typecheck test

docker-test:
	docker build --tag $(HIVEBLOT_DOCKER_TEST_IMAGE) .
	docker run --rm $(HIVEBLOT_DOCKER_TEST_IMAGE) pytest -p no:cacheprovider
