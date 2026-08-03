# ADR 0014: Use Kubernetes for runtime placement, not durable state

## Status

Accepted.

## Decision

Deploy long-lived HiveBlot application and worker processes as Kubernetes `Deployment` resources
and execute finite generic workloads as Kubernetes `Job` resources. Keep PostgreSQL, immutable
object storage, the queue boundary, Temporal, and secret management externally managed in deployed
environments. Kubernetes may replace Pods and retain bounded diagnostic Job objects, but it is not
the source of truth for scientific workflow state, job attempts, annotations, artifacts, or traces.

Add `KubernetesJobExecutor` behind the same domain-independent executor protocol as
`LocalDockerExecutor`. A scheduler Pod leases durable work from PostgreSQL, verifies and stages
immutable inputs on a shared attempt workspace, submits one bounded Job, heartbeats while polling,
captures bounded logs and declared outputs, and publishes results through the existing transactional
job service. The Kubernetes Job has no independent retry policy; `backoffLimit: 0` leaves retries to
the durable attempt state machine. Cancellation and timeout delete the bounded Job, while completed
Job objects receive a TTL.

Use separate service accounts. Only job-controller worker identities may create, inspect, and delete
Jobs and inspect their Pods/logs. Application, workflow, telemetry, and bounded-workload Pods do not
receive Kubernetes API tokens. Apply non-root execution, read-only root filesystems, dropped Linux
capabilities, runtime-default seccomp, explicit resource requests/limits, bounded temporary storage,
and default-deny network policy labels to every workload.

Provide a Kustomize base plus a kind overlay. The kind environment deliberately keeps PostgreSQL and
MinIO in Docker Compose, connected to the cluster's Docker network, to exercise the externally
managed dependency boundary. The production base references a Secret by name but never commits its
contents. GPU jobs may add configured node selectors, tolerations, and `nvidia.com/gpu` resources.

Introduce a real Temporal worker process and external connection settings, but register only a
deterministic runtime-probe workflow in this PR. Scientific workflow definitions remain owned by
later pipeline work rather than being represented as Kubernetes state.

## Consequences

- The same strict job specification can run through local Docker or a Kubernetes Job.
- Pod deletion cannot remove canonical job state or immutable artifacts; an interrupted scheduler
  loses only its lease, which expires through the existing durable recovery path.
- A shared filesystem is an execution transport, not durable artifact storage. It must support
  concurrent mounts in multi-node deployments; kind uses a single-node `ReadWriteOnce` override.
- Unexpected scheduler loss can leave an attempt workspace and bounded Kubernetes Job until its
  deadline/TTL for an operator or future garbage collector. Automatic orphan collection is
  intentionally deferred.
- Network-policy enforcement depends on the cluster CNI. The manifests express intent, while a
  production deployment must select and verify an enforcing CNI.
- Scheduled discovery CronJobs and queue-driven autoscaling are not invented here; PRD-015 and
  PRD-016 own those concrete workloads.
