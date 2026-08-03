# Evidence search development and verification

Evidence search uses PostgreSQL for immutable index metadata, generated full-text vectors,
structured filters, embeddings, citations, frozen evaluation datasets, and evaluation runs. Apply
the normal migration chain by starting the API or by using the existing migration helper.

The local deterministic embedder defaults to 256 dimensions and the logical index name defaults
to `western-blot-evidence`. Override these non-secret settings when needed:

```bash
EVIDENCE_SEARCH_INDEX_NAME=western-blot-evidence
EVIDENCE_SEARCH_EMBEDDING_DIMENSIONS=256
```

The dimension setting must match the embedding identity and every stored document vector. An
existing configuration is immutable; create a new configuration and version when either changes.

## API workflow

All request bodies are strict and carry `schema_version: "1.0"`.

1. `POST /api/v1/evidence-index-configurations` pins retrieval weights and model/tool versions.
2. `POST /api/v1/evidence-index-versions` validates source scopes and artifact metadata, embeds the
   supplied projections, and publishes a ready immutable version.
3. `POST /api/v1/retrieval-evaluation-datasets` freezes queries, relevance judgments, hard
   negatives, filters, and citation expectations.
4. `POST /api/v1/retrieval-evaluation-runs` scores one ready version against one frozen dataset.
5. `POST /api/v1/evidence-index-versions/{index_version_id}/activation` activates only a version
   with a passing run.
6. `POST /api/v1/evidence-search` runs permission-filtered lexical, semantic, or hybrid retrieval.

The first five management operations require the platform operator. After applying migration
`0015`, issue a short-lived token whose plaintext is printed once:

```bash
uv run hiveblot-auth-platform-token \
  --token-label local-search-management \
  --expires-days 1
```

Store it in the local/deployed secret manager and revoke or let it expire after management work.
It is not an organization-administrator token. Ordinary authenticated users receive public
evidence plus private evidence from their active organizations. Explicit inactive-version preview
is also platform-operator only. In authentication-disabled local/test mode, the in-process system
principal provides the same management authority without a bearer token.

## Deterministic tests

```bash
uv run pytest -p no:cacheprovider \
  tests/unit/contracts/test_retrieval_contracts.py \
  tests/unit/search \
  tests/integration/test_search_api.py
uv run hiveblot-schemas --check
```

Run the PostgreSQL constraint and scope test only against an isolated disposable database:

```bash
HIVEBLOT_RUN_LIVE_SEARCH=1 \
DATABASE_URL=postgresql://USER:PASSWORD@127.0.0.1:5432/hiveblot_search_test \
AUTHENTICATION_MODE=disabled \
uv run pytest -p no:cacheprovider tests/integration/test_search_live.py
```

The live test applies every migration, builds a mixed public/private index, proves that SQL scope
filtering occurs before ranking, evaluates and activates the version, rejects a public document
that cites a private artifact, and verifies completed documents and evaluation runs remain
append-only. Never point it at a shared or production database.

## Current limits

Index building is synchronous and request-bounded to 10,000 documents. It is appropriate for the
initial evaluated corpus and local verification, but large backfills should later submit a finite
generic job whose output is published through the same immutable contracts. The initial semantic
provider is a deterministic baseline. A biomedical embedding model must have an immutable model
version and pass frozen retrieval evaluation before activation.
