# Services

Long-lived domain-independent or domain service processes live here.

- `job-service/` owns durable generic job state, leases, heartbeats, retries, cancellation, logs,
  attempts, and result publication. It knows artifacts and container resources but no western-blot
  concepts.

The crawler, durable workflow service, and search service remain deferred to their owning PRDs.
