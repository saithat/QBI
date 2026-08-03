# Kubernetes manifests

`base/` is a credential-free Kustomize base for the API, retained web surface, generic job workers,
Temporal worker, bounded PMC discovery CronJob, OpenTelemetry Collector, RBAC, network policies,
and attempt workspace. It expects
managed PostgreSQL, S3-compatible storage, Temporal, model serving, and a Secret named
`hiveblot-runtime-secrets`.

`overlays/kind/` adapts that base to the single-node local cluster. `kind/` contains bounded cluster
lifecycle scripts and a real finite-job smoke Job. See
`docs/development/kubernetes.md` for setup and verification.

The base intentionally contains no PostgreSQL, object-store, queue, Temporal server, or
application-state StatefulSet. The kind overlay suspends internet-facing scheduled discovery by
default. It contains no KEDA resource; PRD-016 adds queue-driven scaling with fetch workers.
