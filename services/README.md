# Services

Long-lived domain-independent or domain service processes live here.

- `job-service/` owns durable generic job state, leases, heartbeats, retries, cancellation, logs,
  attempts, and result publication. It knows artifacts and container resources but no western-blot
  concepts.
- `crawler/` owns API-first discovery, the durable fetch frontier, HTTP policy, shared source
  limits, and acquisition provenance.
- `search/` owns immutable evidence-index versions, hybrid retrieval, frozen retrieval evaluation,
  and activation gates. It consumes explicit scientific evidence contracts and does not generate
  claims.

The job service now supports local Docker and Kubernetes Job executors behind one protocol. Temporal
workflow workers live in `workers/workflow` and connect to an externally managed Temporal service.
