# Remove fixed development credentials

## Incident summary

GitGuardian reported a generic-password finding after a stacked feature branch was pushed. A
redacted audit of every reachable Git object found no provider token, private key, or high-entropy
credential. The triggering branch added runtime `SecretStr` references but no literal secret. The
repository did, however, inherit fixed local Docker, PostgreSQL, MinIO, and model API placeholder
values from the prototype.

These values were intended only for loopback development. Keeping shared credential-shaped
defaults in tracked files creates needless scanner findings and makes accidental reuse possible,
so this change removes them even though the reported value is not treated as a live credential.

## Changes

- All tracked environment templates leave credential fields blank.
- `make local-env` creates independent random local values in ignored `.env`, with mode `0600`.
- The generator atomically creates the file and refuses to overwrite existing configuration.
- Docker Compose requires explicit generated credentials instead of using fixed fallbacks.
- Pydantic Settings no longer embed database, model API, or object-store credentials.
- Tests enforce blank templates, credential-free defaults, file permissions, correlated MinIO/S3
  values, and overwrite refusal.

## Verification

```bash
uv run pytest -p no:cacheprovider \
  tests/unit/test_settings.py tests/unit/test_local_env_bootstrap.py

make local-env
docker compose config --quiet
```

The second command is create-only. Use a temporary checkout or choose explicit `--output` and
`--template` paths when verifying it more than once.

## History and rotation

The old values remain in already-published commits. Because the audit identifies them as public
development placeholders rather than live secrets, this hotfix does not rewrite shared history.
The GitGuardian occurrence should be resolved as a false positive after confirming its location
matches one of those placeholder assignments. If any placeholder was reused outside the isolated
local stack, rotate that external credential first; history rewriting is not a substitute for
rotation.

## Limitations

- This change does not rotate credentials in external services.
- It does not overwrite or inspect an existing ignored `.env`.
- It does not add a third-party secret scanner; repository-host scanning remains an independent
  quality gate.
