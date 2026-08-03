# Distributed public-source fetching

```text
eligible crawl_frontier row
          |
 durable crawl_fetch_task  <---- KEDA reads queue depth
          |
 lease + append-only attempt
          |
 explicit host allowlist
          |
 shared PostgreSQL domain policy/permit
          |
 robots policy (controlled crawl only)
          |
 conditional bounded HTTP request + redirects
          |
 immutable content-addressed artifact
          |
 frontier acquisition + downstream parse outbox
```

## Durable delivery

Fetch tasks are distinct from frontier identity records and generic container jobs. A worker lease
contains the canonical URL, source domain, acquisition method, expected media types, trace ID,
ETag/Last-Modified validators, attempt number, and expiration. Leasing increments both the task and
frontier attempt state in one transaction. Heartbeats extend both rows. Expired leases produce an
append-only `lease_expired` attempt and either retry or dead-letter according to the task's bounded
attempt policy.

Late workers cannot publish through expired lease tokens. Artifact publication is content-addressed
and completion uses unique frontier/artifact and fetch-task/outbox keys, so redelivery cannot create
multiple canonical artifacts or downstream handoffs.

## Source safety and politeness

Every request destination, including redirects and robots locations, must exactly match
`FETCH_ALLOWED_HOSTS`. Controlled-crawl tasks fetch and cache `/robots.txt` before content access;
official APIs, accession downloads, and open archives carry explicit `not_applicable` robots state.
Responses enforce timeouts, redirect bounds, declared and decoded byte limits, and expected media
types. HTTP 401/403 and robots denials become prohibited; transient transport errors, 408, 425, 429,
and 5xx responses retry. `Retry-After` accepts both delay-seconds and HTTP-date forms.

`crawl_domain_policies` stores the minimum start interval and maximum concurrent permits. Acquiring
a permit locks one domain row and counts unexpired permits, so replica count cannot multiply a
source's allowance. Permits expire after worker loss.

## Provenance and handoff

Each attempt stores request/final URLs, response status/media/hash/bytes, latency, deduplication,
retry time, error code, worker, trace, and timestamps. Successful bytes are published through the
immutable artifact service first. Only then does one transaction link the artifact, mark the
frontier acquired, persist conditional validators, and enqueue one parse outbox task.

Metrics aggregate queue depth, leases, active workers, retries, dead letters, fetch count/latency,
HTTP status, bytes, source errors, and artifact deduplication. The API exposes the snapshot plus task
and attempt provenance. KEDA's optional PostgreSQL scaler targets the durable queue, with one
minimum replica retained to enqueue newly eligible frontier rows.
