# Configuration

`hiveblot.settings.Settings` uses Pydantic Settings. Environment variables override `.env`.
Secrets use `SecretStr`, and settings objects are immutable.

Profiles:

- `local`: local defaults are allowed for fast development.
- `test`: use explicit isolated database, model, and data paths; see `.env.test.example`.
- `deployed`: database, model, and S3 endpoint/bucket/credential values must be explicitly supplied.
  Local database credentials, placeholder model/storage keys, and localhost model endpoints are
  rejected.

Artifact URL lifetimes and maximum byte size are bounded settings. HTTP source ingestion remains
disabled until `SOURCE_INGEST_ALLOWED_HOSTS` contains an exact, comma-separated host allowlist.

`.env.deployed.example` is a shape-only template. Never fill it and commit it. Production values
must come from the deployment's secret and configuration systems. No code should log
`Settings.model_dump()` with unredacted secrets or pass secrets through job contracts.
