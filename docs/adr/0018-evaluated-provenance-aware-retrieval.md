# ADR 0018: Evaluate immutable evidence indexes before activation

- Status: accepted
- Date: 2026-08-02

## Decision

Build each evidence index as an immutable, versioned collection of explicit observations and
claims. Every indexed document must name a reviewed annotation revision or a published pipeline
result, carry at least one immutable artifact citation, and retain its exact visibility scope.
Index configuration, embedding identity, optional reranker identity, source documents, and the
content-derived manifest hash are stored with the version.

Use PostgreSQL full-text ranking for the lexical candidate channel and a versioned embedding
provider for the semantic channel. Apply structured and organization-scope predicates in
PostgreSQL before candidates leave persistence. The service repeats the scope check defensively,
combines the configured channel scores, and may apply a versioned reranker.

Keep new index versions inactive. A version may move from `building` to `ready`, then to `active`
only after a frozen retrieval dataset produces a passing, immutable evaluation run. Activating a
new version records the exact passing run used for activation and atomically retires the prior
active version for the same index name. PostgreSQL triggers enforce the lifecycle and the
evaluation gate.

## Why

Search results are scientific evidence, not anonymous text snippets. A result without a producing
revision, exact citation, quality label, and review state cannot be independently inspected.
Likewise, a private correction must never become visible merely because it was embedded in a
shared index. Pre-filtering before scoring prevents private candidates from affecting ranks or
appearing in logs and response construction.

Index activation is a scientific publication decision. Frozen judgments and versioned scorers
make recall, ranking, filter behavior, citation behavior, and cross-tenant leakage reproducible
before traffic moves to a new implementation.

## Consequences

- The initial semantic provider is a deterministic, offline token and character-ngram hashing
  embedder. Its interface can accept a pinned biomedical model later without changing contracts.
- Index documents are projections, never canonical scientific records. Changes to source reviews
  or publications require a new index version; completed documents cannot be edited.
- Public index documents cannot cite private artifacts or derive from private annotations or
  publications. Private documents must remain in the same organization as every private source.
- Search callers receive public documents plus documents belonging to their active organizations.
  A short-lived token for the reserved platform-operator identity is required to build, preview,
  evaluate, and activate index versions in bearer mode; organization roles never inherit it.
- Retrieval datasets and runs are append-only. Activation requires one passing run but operators
  remain responsible for choosing representative frozen judgments and meaningful thresholds.
- PRD-018 exposes an API-first surface. Large asynchronous index builds and a search-specific web
  interface remain future work; synchronous builds are capped at 10,000 documents per request.
