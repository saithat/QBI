# Kubernetes manifests

`base/` is a credential-free Kustomize base for the API, retained web surface, generic job workers,
Temporal worker, OpenTelemetry Collector, RBAC, network policies, and attempt workspace. It expects
managed PostgreSQL, S3-compatible storage, Temporal, model serving, and a Secret named
`hiveblot-runtime-secrets`.

`overlays/kind/` adapts that base to the single-node local cluster. `kind/` contains bounded cluster
lifecycle scripts and a real finite-job smoke Job. See
`docs/development/kubernetes.md` for setup and verification.

The base intentionally contains no PostgreSQL, object-store, queue, Temporal server, or
application-state StatefulSet. It also contains no discovery CronJob or KEDA resource; PRD-015 and
PRD-016 add those only with their concrete source/frontier semantics.
