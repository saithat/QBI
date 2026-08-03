# Configuration

`hiveblot.settings.Settings` uses Pydantic Settings. Environment variables override `.env`.
Secrets use `SecretStr`, and settings objects are immutable.

Profiles:

- `local`: local defaults are allowed for fast development.
- `test`: use explicit isolated database, model, and data paths; see `.env.test.example`.
- `deployed`: database, model, and S3 endpoint/bucket/credential values must be explicitly supplied.
  Local database credentials, placeholder model/storage keys, and localhost model endpoints are
  rejected. Temporal address, namespace, and task queue must also be explicit.

Artifact URL lifetimes and maximum byte size are bounded settings. HTTP source ingestion remains
disabled until `SOURCE_INGEST_ALLOWED_HOSTS` contains an exact, comma-separated host allowlist.

`.env.deployed.example` is a shape-only template. Never fill it and commit it. Production values
must come from the deployment's secret and configuration systems. No code should log
`Settings.model_dump()` with unredacted secrets or pass secrets through job contracts.

All tracked environment templates are credential-free. Run `make local-env` to generate a
gitignored, mode-`0600` local file. `docker compose` requires those generated values and does not
fall back to shared passwords or object-store keys. The generator is create-only and will not
replace an existing `.env`.

`JOB_EXECUTOR` selects `local-docker` or `kubernetes-job`. Kubernetes settings identify the
namespace, attempt-workspace PVC/root, bounded-job service account, poll/API timeout, completed-Job
TTL, and optional GPU selector/toleration. A GPU selector key and value must be configured together.

`TEMPORAL_ADDRESS`, `TEMPORAL_NAMESPACE`, `TEMPORAL_TASK_QUEUE`, `TEMPORAL_TLS`, and the secret
`TEMPORAL_API_KEY` configure the external workflow service. The Kubernetes base contains shape-only
endpoints and references `hiveblot-runtime-secrets`; deployments must replace all example endpoint
values and supply that Secret out of band.
