# Generic job service and local Docker execution

```text
strict JobSpecification + canonical immutable input references
                         |
          PostgreSQL pending job + idempotency key
                         |
        atomic lease (FOR UPDATE SKIP LOCKED)
                         |
        attempt row + worker heartbeat/lease expiry
                         |
 verified read-only inputs -> restricted container -> isolated outputs
                         |
       queryable stdout/stderr/system log records
                         |
  immutable output artifacts with derived-from relationships
                         |
 strict terminal JobResult + idempotent canonical publication
```

The service is deliberately domain-independent. `hiveblot_job_service` knows job IDs, attempts,
leases, resources, containers, artifacts, outputs, traces, errors, and cancellation. It does not
import evaluation or extraction packages and has no concepts for papers, figures, lanes, proteins,
bands, conditions, or measurements. The preserved western-blot normalization operation and its job
factory live in `workers/jobs`.

## Contracts and durable state

`JobSpecification`, `JobLease`, `JobRecord`, `JobAttemptRecord`, `JobLogRecord`, and the
discriminated `JobResult` union are strict, frozen, versioned Pydantic contracts. Inputs and outputs
have safe unique names. Output-producing jobs require at least one provenance input. Resources,
timeout, maximum attempts, retry backoff, network policy, environment allowlist, pipeline identity,
parent job, and trace ID are explicit.

PostgreSQL is the pending queue and source of truth. A worker atomically leases one eligible row
with `FOR UPDATE SKIP LOCKED`, and every lease creates a separate immutable attempt identity. A
heartbeat extends both the active job lease and attempt record. Expired attempts become
`lease_expired`; the job becomes pending again or dead-lettered when attempts are exhausted. A
cancel request immediately terminates pending work and marks active work for cooperative container
shutdown. The transaction rejects success after cancellation.

Terminal attempt results are never replaced. Retryable failures return the job to pending after a
bounded exponential delay while retaining the failed attempt and its logs. Successful canonical
outputs use a `(job_id, output_name)` uniqueness boundary. Publishing the exact same result again is
idempotent; publishing different bytes or a different result for that attempt fails.

## LocalDockerExecutor security boundary

The executor reads every input through `VerifiedArtifactReader`, which checks stored metadata,
length, and SHA-256. Each attempt receives a private temporary workspace with:

- an `/inputs` bind mount whose files and mount are read-only;
- an isolated writable `/outputs` bind mount;
- a read-only container root filesystem and bounded temporary `/tmp`;
- a non-root numeric user, all Linux capabilities dropped, `no-new-privileges`, and a PID limit;
- declared CPU and memory limits;
- networking disabled by default and enabled only by an explicit contract value; and
- an empty container environment except for names both allowlisted by the job and supplied by the
  worker.

Docker is invoked with an argument vector and never through a shell. Standard output and error are
captured to files to avoid pipe deadlock, bounded before persistence, and queryable by attempt.
Timeout or cancellation stops the named container. Only declared regular output files are read;
symlinks, directories, undeclared names, missing required files, and configured size-limit breaches
are rejected.

The worker derives output visibility from its exact inputs. Public inputs produce public output. If
any input is organization-private, output remains private to that one organization. Inputs from
multiple private organizations are rejected. Every output is content-addressed and linked
`derived_from` every input artifact before the success result can become canonical.

## Failure and recovery behavior

- A worker crash stops heartbeats; lease expiry makes the job safely retryable.
- A duplicate submission returns the existing record only when its canonical specification hash is
  identical; a conflicting ID or idempotency key fails.
- A duplicate terminal publication returns the existing record only when the result is identical.
- Non-retryable failures enter `failed`; exhausted retryable failures enter `dead_letter`.
- Cancellation cannot be converted to success by a stale worker.
- Container output publication may leave an immutable, non-canonical artifact if cancellation wins
  a final race; it never changes the job result or overwrites an earlier output.

## Kubernetes executor

`KubernetesJobExecutor` implements the same domain-independent execution protocol. It maps exact job
resources and security policy to one bounded Kubernetes Job, stages verified inputs read-only on an
attempt workspace, heartbeats the PostgreSQL lease, captures Pod logs, validates declared outputs,
and returns bytes to the same idempotent artifact publication transaction. Kubernetes retries are
disabled so each retry remains a new durable HiveBlot attempt.

Durable multi-operation and human-pause orchestration remains a separate Temporal workflow layer.
See `docs/architecture/kubernetes-runtime.md`.
