# Local Kubernetes development

The local runtime uses kind for application placement and Docker Compose for PostgreSQL and MinIO.
This preserves the deployed boundary: durable dependencies are reachable from Kubernetes but do not
run as Pods.

## Prerequisites

- Docker Engine with Compose
- `kind`
- `kubectl`
- `rg`
- Python 3.12 and `uv`

The Compose CLI must support the `!reset` override tag; the kind dependency overlay uses it to
remove the root stack's host-port publications.

For temporary/nonstandard binary locations, set `HIVEBLOT_KIND_BIN` and
`HIVEBLOT_KUBECTL_BIN` to their absolute paths.

Create the ignored local environment once, then build and start the runtime:

```bash
make setup
make local-env
make kind-up
```

If `.env` already exists, `make local-env` intentionally refuses to replace it. The kind bootstrap
reads only the required values, writes a temporary mode-`0600` Secret input, applies the Secret, and
removes the temporary file. No Secret values are committed or rendered into Kustomize output.

`make kind-up` performs these steps:

1. creates the dedicated `hiveblot-kind` Docker network;
2. starts only PostgreSQL and MinIO with Compose on that network and waits for both health checks;
3. creates the `hiveblot` kind cluster on the same network if absent;
4. builds and loads `hiveblot:prd-017` into kind;
5. applies the runtime Secret and kind Kustomize overlay; and
6. waits for enabled Deployments to roll out.

The kind overlay scales the Temporal worker to zero because no in-cluster Temporal server is
provided. Point settings at an external Temporal endpoint and remove that replica patch to exercise
the workflow worker. It also permits host ingress to the local NodePort; the production base limits
HTTP ingress to namespace-selected cluster sources. The fetch Deployment keeps one long-lived
worker; external acquisition still requires an intentional `FETCH_ALLOWED_HOSTS` value.

Inspect the runtime:

```bash
kubectl get pods,jobs,services --namespace hiveblot
kubectl get events --namespace hiveblot --sort-by=.lastTimestamp
curl http://127.0.0.1:18080/health/ready
```

Run the end-to-end finite-job acceptance check:

```bash
make kind-smoke
```

The smoke Job uploads the historic raw model-output fixture through the multipart artifact API,
submits the preserved normalization operation through the generic job API, waits for a dynamically
created Kubernetes Job, downloads its immutable output through a signed URL, and verifies exactly
40 normalized records.

Shut down the exact local resources:

```bash
make kind-down
```

This deletes the named kind cluster, the Compose dependency project, and the dedicated Docker
network. It preserves the dependency project's named PostgreSQL/MinIO volumes for the next run and
does not delete tracked files or any unrelated cluster/project.

## Manifest and unit verification

Render either configuration without contacting a cluster:

```bash
kubectl kustomize infra/kubernetes/base
kubectl kustomize infra/kubernetes/overlays/kind
kubectl kustomize infra/kubernetes/addons/keda
uv run pytest -p no:cacheprovider tests/unit/kubernetes tests/unit/jobs/test_kubernetes_executor.py
```

The manifest tests assert workload probes, resources, Pod hardening, token use, RBAC scope, external
durable-state boundaries, network policies, workspace shape, and credential-free templates. The
executor tests cover manifest translation, GPU placement, timeout/cancellation, API normalization,
and publication through the durable in-memory job service.

The optional KEDA add-on uses its PostgreSQL scaler against eligible `crawl_fetch_tasks`. Install a
compatible externally managed KEDA release before applying it:

```bash
kubectl apply -k infra/kubernetes/addons/keda
```

KEDA changes fetch-worker replica count only. Shared PostgreSQL domain policies and permits remain
authoritative, so scale-out cannot increase one source's request rate.

## Production adaptation checklist

- Replace example ConfigMap endpoints with managed PostgreSQL, object storage, model, Temporal, and
  telemetry endpoints.
- Create `hiveblot-runtime-secrets` through the deployment's secret manager; do not commit it.
- Select a multi-node storage class that supports the attempt workspace access mode, or replace the
  staging transport while retaining immutable S3 publication.
- Verify the cluster CNI enforces NetworkPolicy.
- Pin application and collector images to reviewed immutable digests in the release pipeline.
- Configure ingress/TLS, Pod disruption policy, autoscaling, observability export, and backup/restore
  outside this local overlay.
- Install and monitor KEDA before applying `addons/keda`, and give its PostgreSQL identity read-only
  access to the queue-depth query.
- Configure GPU labels, tolerations, device plugin, quotas, and resource limits before accepting GPU
  jobs.
