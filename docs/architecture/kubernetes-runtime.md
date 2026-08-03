# Kubernetes runtime

```text
                    externally managed durable systems
          PostgreSQL | S3-compatible storage | Temporal | secrets
                         ^             ^
                         | durable API |
                         |             |
  API/Web Deployments ---+             +--- Workflow-worker Deployment
                         |
     durable pending job + immutable artifact references
                         |
     scheduler/evaluation/CPU worker Deployment
                         |
        verified input staging + Kubernetes batch/v1 API
                         |
              one bounded Kubernetes Job
           /inputs (read-only)  /outputs (writable)
                         |
       logs + declared bytes -> durable attempt publication
```

Kubernetes owns process placement and replacement. PostgreSQL remains authoritative for job leases,
attempts, cancellation, retries, logs, and canonical results; object storage remains authoritative
for immutable scientific bytes. Neither a Pod nor a Job is treated as scientific workflow history.

## Runtime resources

The Kustomize base under `infra/kubernetes/base` defines:

- API and retained web-surface Deployments and Services;
- job scheduler, evaluation-worker, CPU-worker, and Temporal workflow-worker Deployments;
- an OpenTelemetry Collector Deployment and Service;
- separate service accounts and namespace-local, least-privilege job-controller RBAC;
- a shared ephemeral attempt-workspace PVC;
- startup, readiness, and liveness probes plus explicit resource requests and limits; and
- default-deny network policy with explicit ingress and label-selected egress exceptions.

API liveness and startup probes report process health without testing dependencies. Readiness checks
PostgreSQL so a Pod leaves Service endpoints when it cannot serve durable application state.
Long-lived worker probes verify the worker process is alive; failure to connect to its external
dependency causes the process to exit and lets Kubernetes replace it.

The web Deployment currently runs the retained monolith ASGI entry point. It gives the web surface a
separate runtime and Service without pretending the existing frontend is already an independently
built application. Splitting the frontend image is a later packaging concern.

## Generic Kubernetes execution

`KubernetesJobExecutor` consumes the same `JobLease` used by the local Docker executor. It:

1. creates an attempt-specific workspace;
2. reads every input through the hash-verifying artifact reader;
3. writes inputs read-only and creates a writable declared-output directory;
4. submits a uniquely named Job annotated with job, attempt, and trace identifiers;
5. maps canonical CPU, memory, GPU, command, argument, environment, timeout, and network policy
   fields into the Pod;
6. heartbeats the durable lease while polling Job conditions;
7. deletes the Job on cancellation or timeout;
8. captures bounded Pod logs and validates declared regular output files; and
9. returns bytes to the existing idempotent publication transaction.

`backoffLimit: 0` prevents Kubernetes from creating opaque application retries. A failed attempt is
recorded once, after which the durable job service decides whether and when to lease a new attempt.
`activeDeadlineSeconds` mirrors the canonical timeout and `ttlSecondsAfterFinished` bounds diagnostic
Job retention.

Schedulers and bounded Jobs share only attempt-scoped input/output directories on the workspace PVC.
Canonical artifacts are always republished to object storage before a job succeeds. A production
multi-node storage class must support the required concurrent mounts. The kind overlay is one node
and patches the claim to `ReadWriteOnce`.

## Security and placement

Job-controller RBAC is limited to namespaced Jobs, Pods, and Pod logs. Bounded Jobs use a tokenless
runner identity. All containers run as non-root with no privilege escalation, a read-only root,
runtime-default seccomp, and all capabilities dropped. Only `/tmp` and declared output storage are
writable.

Canonical `NetworkPolicy.DENY` Jobs receive a `hiveblot.io/network-access=deny` label and remain
under default-deny egress. Explicitly allowed jobs receive the allow label. This is enforceable only
on a cluster whose CNI implements NetworkPolicy.

GPU count becomes an extended `nvidia.com/gpu` request and limit. Optional configuration adds a GPU
node selector and toleration only to GPU jobs; CPU jobs remain portable.

## Durable workflows and telemetry

`hiveblot-workflow-worker` connects to external Temporal with explicit address, namespace, task
queue, TLS, and optional API-key settings. PRD-014 registers a deterministic runtime probe so image,
SDK, and worker connectivity can be tested without prematurely designing domain workflows.

The OpenTelemetry Collector accepts OTLP gRPC/HTTP and currently exports bounded debug output. The
manifests propagate service and trace identifiers, but application-wide automatic instrumentation
and a production telemetry backend remain follow-up work.
