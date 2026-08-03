# ADR 0016: PostgreSQL fetch queue and shared domain permits

- Status: accepted
- Date: 2026-08-02

## Decision

Use PostgreSQL as the first durable crawl-task queue and coordination point. Long-lived fetch-worker
Deployments lease tasks with row locks and heartbeats. A separate domain-policy row and expiring
permit table enforce a shared minimum request interval and maximum concurrency across every worker
replica. KEDA may scale the Deployment from a PostgreSQL queue-depth query, but never changes a
domain policy.

Store every fetch attempt, HTTP status, timing, byte count, retry decision, conditional-request
validator, error, trace, and output artifact reference. Publish successful raw bytes through the
immutable artifact service before transactionally completing the task and inserting a downstream
parse outbox record.

## Why

HiveBlot already requires managed PostgreSQL for durable scientific state. A transactional queue
keeps frontier state, attempt history, artifact linkage, and downstream handoff atomic without
introducing a second local-development broker in this PR. The queue and rate-limiter interfaces are
explicit, so a managed broker or dedicated rate-limit service can replace their persistence without
changing the HTTP worker.

The domain limiter is deliberately outside individual Pods. Increasing replicas can increase
cross-domain throughput, but it cannot increase one domain's configured start rate or concurrency.

## Consequences

- Production database roles must grant workers only the crawl/artifact operations they require.
- KEDA is an optional cluster add-on; the base remains valid without its CRDs and keeps one worker.
- PostgreSQL queue load must be monitored and can be moved behind the existing repository boundary.
- Host allowlisting is mandatory, and controlled crawling additionally evaluates cached robots
  rules. Redirect targets are checked against the same allowlist.
- This PR publishes parse outbox records; modality-specific parsing remains a downstream concern.
