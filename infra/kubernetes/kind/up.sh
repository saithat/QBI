#!/usr/bin/env bash
set -euo pipefail

cluster_name="hiveblot"
network_name="hiveblot-kind"
image_name="hiveblot:prd-017"
kind_bin="${HIVEBLOT_KIND_BIN:-kind}"
kubectl_bin="${HIVEBLOT_KUBECTL_BIN:-kubectl}"

for command_name in docker "${kind_bin}" "${kubectl_bin}" rg; do
  if ! command -v "${command_name}" >/dev/null 2>&1; then
    echo "Required command is missing: ${command_name}" >&2
    exit 2
  fi
done

if [[ ! -f .env ]]; then
  echo "Missing .env; run 'make local-env' first." >&2
  exit 2
fi

if ! docker network inspect "${network_name}" >/dev/null 2>&1; then
  docker network create "${network_name}" >/dev/null
fi

docker compose \
  --env-file .env \
  --project-name hiveblot-kind \
  -f compose.yaml \
  -f infra/kubernetes/kind/compose.dependencies.yaml \
  up --build -d --wait --wait-timeout 120 postgres minio

if ! "${kind_bin}" get clusters | rg -Fxq "${cluster_name}"; then
  KIND_EXPERIMENTAL_DOCKER_NETWORK="${network_name}" \
    "${kind_bin}" create cluster \
      --name "${cluster_name}" \
      --config infra/kubernetes/kind/cluster.yaml
fi

docker build -t "${image_name}" .
"${kind_bin}" load docker-image "${image_name}" --name "${cluster_name}"

"${kubectl_bin}" apply -f infra/kubernetes/base/namespace.yaml
secret_directory="$(mktemp -d /tmp/hiveblot-kind-secret.XXXXXX)"
trap 'rm -f "${secret_directory}/runtime.env"; rmdir "${secret_directory}"' EXIT
.venv/bin/python scripts/render_kubernetes_secret_env.py \
  --input .env \
  --output "${secret_directory}/runtime.env"
"${kubectl_bin}" create secret generic hiveblot-runtime-secrets \
  --namespace hiveblot \
  --from-env-file="${secret_directory}/runtime.env" \
  --dry-run=client \
  -o yaml | "${kubectl_bin}" apply -f -
"${kubectl_bin}" apply -k infra/kubernetes/overlays/kind

for deployment_name in \
  hiveblot-api \
  hiveblot-web \
  hiveblot-job-scheduler \
  hiveblot-evaluation-workers \
  hiveblot-cpu-workers \
  hiveblot-otel-collector; do
  "${kubectl_bin}" rollout status \
    "deployment/${deployment_name}" \
    --namespace hiveblot \
    --timeout=180s
done

echo "HiveBlot kind runtime is ready at http://127.0.0.1:18080"
