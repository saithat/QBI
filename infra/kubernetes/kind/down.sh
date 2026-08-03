#!/usr/bin/env bash
set -euo pipefail

kind_bin="${HIVEBLOT_KIND_BIN:-kind}"

"${kind_bin}" delete cluster --name hiveblot
docker compose \
  --env-file .env \
  --project-name hiveblot-kind \
  -f compose.yaml \
  -f infra/kubernetes/kind/compose.dependencies.yaml \
  down
docker network rm hiveblot-kind >/dev/null 2>&1 || true
