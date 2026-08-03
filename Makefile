HIVEBLOT_KUBECTL_BIN ?= kubectl

.PHONY: up down logs setup local-env kind-up kind-smoke kind-down format lint typecheck schemas schema-check test check docker-test ingest

up:
	docker compose up --build -d

down:
	docker compose down

logs:
	docker compose logs -f app minio vllm

setup:
	uv sync --locked --all-extras

local-env:
	uv run python scripts/bootstrap_local_env.py

kind-up:
	bash infra/kubernetes/kind/up.sh

kind-smoke:
	$(HIVEBLOT_KUBECTL_BIN) delete job hiveblot-runtime-smoke --namespace hiveblot --ignore-not-found
	$(HIVEBLOT_KUBECTL_BIN) apply -f infra/kubernetes/kind/smoke-job.yaml
	$(HIVEBLOT_KUBECTL_BIN) wait --for=condition=complete job/hiveblot-runtime-smoke --namespace hiveblot --timeout=240s
	$(HIVEBLOT_KUBECTL_BIN) logs job/hiveblot-runtime-smoke --namespace hiveblot

kind-down:
	bash infra/kubernetes/kind/down.sh

format:
	uv run ruff format .
	uv run ruff check --fix .

lint:
	uv run ruff format --check .
	uv run ruff check --no-cache .

typecheck:
	uv run mypy apps hiveblot packages/contracts/src/hiveblot_contracts \
		packages/densitometry/src/hiveblot_densitometry \
		packages/evaluation/src/hiveblot_evaluation \
		packages/extraction/src/hiveblot_extraction packages/storage/src/hiveblot_storage \
		services/crawler/src/hiveblot_crawler \
		services/job-service/src/hiveblot_job_service workers

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
