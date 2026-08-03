# Configuration

`hiveblot.settings.Settings` uses Pydantic Settings. Environment variables override `.env`.
Secrets use `SecretStr`, and settings objects are immutable.

Profiles:

- `local`: local defaults are allowed for fast development.
- `test`: use explicit isolated database, model, and data paths; see `.env.test.example`.
- `deployed`: database, model, and S3 endpoint/bucket/credential values must be explicitly supplied.
  Local database credentials, placeholder model/storage keys, and localhost model endpoints are
  rejected. Temporal address, namespace, and task queue must also be explicit. Public discovery
  must provide an identifying `DISCOVERY_USER_AGENT` rather than the local placeholder.

`AUTHENTICATION_MODE` is `disabled` only for explicit local/test use. Deployed settings require
`bearer` and an `AUTH_TOKEN_PEPPER` of at least 32 characters. The pepper keys stored token digests;
rotating it invalidates existing bearer tokens. Keep it in the deployment secret manager, never in
a tracked environment template. See `docs/development/authorization.md`.

Artifact URL lifetimes and maximum byte size are bounded settings. HTTP source ingestion remains
disabled until `SOURCE_INGEST_ALLOWED_HOSTS` contains an exact, comma-separated host allowlist.

`.env.deployed.example` is a shape-only template. Never fill it and commit it. Production values
must come from the deployment's secret and configuration systems. No code should log
`Settings.model_dump()` with unredacted secrets or pass secrets through job contracts.

All tracked environment templates are credential-free. Run `make local-env` to generate a
gitignored, mode-`0600` local file, including an independent random token pepper. `docker compose`
requires those generated values and does not
fall back to shared passwords or object-store keys. The generator is create-only and will not
replace an existing `.env`.

`JOB_EXECUTOR` selects `local-docker` or `kubernetes-job`. Kubernetes settings identify the
namespace, attempt-workspace PVC/root, bounded-job service account, poll/API timeout, completed-Job
TTL, and optional GPU selector/toleration. A GPU selector key and value must be configured together.

`TEMPORAL_ADDRESS`, `TEMPORAL_NAMESPACE`, `TEMPORAL_TASK_QUEUE`, `TEMPORAL_TLS`, and the secret
`TEMPORAL_API_KEY` configure the external workflow service. The Kubernetes base contains shape-only
endpoints and references `hiveblot-runtime-secrets`; deployments must replace all example endpoint
values and supply that Secret out of band.

`PMC_OAI_BASE_URL`, `DISCOVERY_USER_AGENT`, `DISCOVERY_HTTP_TIMEOUT_SECONDS`,
`DISCOVERY_MAX_RESPONSE_BYTES`, `DISCOVERY_LOOKBACK_DAYS`, and `DISCOVERY_MAXIMUM_PAGES` configure
bounded API-first paper discovery. The user agent should contain a monitored contact or project URL.
See `docs/development/public-discovery.md`; do not place credentials in a user agent.

`FETCH_ALLOWED_HOSTS` is the mandatory exact host allowlist for long-lived fetch workers. Fetch
lease/attempt, timeout, response/robots byte, redirect, shared interval/concurrency, permit, and
polling settings are independently bounded by `FetchWorkerSettings`, which does not load model,
Temporal, or Kubernetes API credentials. See `docs/development/distributed-fetching.md`.

`EVIDENCE_SEARCH_INDEX_NAME` selects the logical active evidence index and
`EVIDENCE_SEARCH_EMBEDDING_DIMENSIONS` selects the installed deterministic embedding width. Both
are non-secret, bounded settings. Changing either requires a new immutable index configuration and
version; an existing stored configuration is never edited. See
`docs/development/evidence-search.md`.
