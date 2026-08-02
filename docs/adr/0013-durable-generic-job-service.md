# ADR 0013: Separate generic execution from scientific workflows

## Status

Accepted.

## Decision

Create a domain-independent job service backed by PostgreSQL for finite computational work. A job
contains only a versioned container specification, bounded resources, immutable artifact inputs,
declared outputs, timeout and retry policy, trace lineage, and cancellation state. Use explicit
worker leases and heartbeats. Preserve every attempt, result, and bounded log record, and make
terminal result/output publication transactional and idempotent.

Use `LocalDockerExecutor` for PRD-013. It materializes hash-verified artifacts into read-only input
mounts and runs an argv-only Docker command as a non-root user with a read-only root, dropped
capabilities, `no-new-privileges`, CPU/memory/PID limits, isolated output and temporary mounts,
restricted environment variables, and no network by default. Publish declared outputs through the
existing immutable artifact service with complete `derived_from` provenance.

Keep scientific behavior in worker-owned operations. The initial integration wraps the retained
legacy western-blot normalization as a bounded operation under `workers/jobs`; neither job storage
nor execution imports western-blot domain code.

Do not use Docker or Kubernetes state as durable workflow state. PostgreSQL owns job and attempt
state; S3-compatible storage owns immutable bytes. Keep multi-operation scientific orchestration,
human pauses, and selective workflow replay in the future Temporal layer. Add a
`KubernetesJobExecutor` without changing canonical job contracts in PRD-014.

## Consequences

- Worker termination is recoverable through lease expiration, and retry history remains visible.
- Stale or cancelled workers cannot publish success.
- Duplicate delivery and result publication do not duplicate canonical outputs.
- The scheduler can execute extraction, densitometry, indexing, or future bounded tools without
  learning their domain entities.
- Local execution requires the host worker and Docker daemon to agree on bind-mount paths. Mounting
  the Docker socket into the API is intentionally unsupported.
- PostgreSQL is a practical initial queue and consistency boundary. Queue abstraction and
  Kubernetes execution can be added without changing scientific contracts or history.
