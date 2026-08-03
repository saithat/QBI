# Evidence retrieval architecture

HiveBlot indexes explicit scientific evidence projections, not raw model prose. The source of an
index document is either an immutable annotation revision or an explicitly published pipeline
result. Its citations point to content-addressed artifacts and may include page and source-pixel
geometry.

```text
reviewed annotation revision or published pipeline result
                         |
              strict EvidenceIndexDocument
                         |
       versioned configuration + deterministic embedding
                         |
          immutable building index version
                         |
     manifest hash + relational/provenance validation
                         v
                       ready
                         |
       frozen judgments -> versioned retrieval scoring
                         |
             passing activation gate
                         v
                       active
```

## Stored boundaries

- `evidence_index_configurations` stores immutable lexical, semantic, candidate, and optional
  reranker settings with model/tool identity and a configuration hash.
- `evidence_index_versions` stores lifecycle state and the content-derived manifest hash. At most
  one version of an index name is active.
- `evidence_index_documents` stores the strict JSON projection, normalized structured terms,
  generated PostgreSQL `tsvector`, deterministic embedding, source revision/publication, and exact
  organization scope.
- `evidence_index_document_citations` makes artifact provenance relationally enforceable rather
  than relying only on nested JSON.
- `retrieval_evaluation_datasets` and `retrieval_evaluation_runs` store frozen, append-only
  judgments and results.

The database checks that relational identity, scope, terms, source references, citation rows,
embedding dimensions, and canonical JSON agree. Documents may be inserted only while their index
version is building; completed versions and evaluation records are immutable.

## Retrieval path

1. The API resolves the authenticated principal and requires the `search` permission.
2. PostgreSQL selects only public documents and organization-private documents in the principal's
   active organizations. Protein, biological-system, treatment, condition, review-state, and
   evidence-quality filters are applied in the same query.
3. PostgreSQL calculates English full-text rank and cosine candidate rank over the already scoped
   rows, then applies the requested lexical, semantic, or hybrid mode before the candidate limit.
4. The service rejects any out-of-scope candidate defensively, recomputes cosine similarity using
   the configuration's pinned embedder, combines the configured channel weights, and optionally
   adds the pinned reranker score.
5. The response returns citations, review and quality state, supporting observations,
   contradictory observations, component scores, index version, trace identifier, and elapsed
   time.

The initial deterministic semantic provider hashes normalized tokens and character trigrams into
a unit vector. It normalizes common western-blot terminology such as `immunoblot` and `WB`. This is
a reproducible baseline, not a claim that hashing vectors match a trained biomedical embedding
model.

## Evaluation and activation

A retrieval-evaluation query contains graded relevant documents, hard negatives, expected
structured filters, required citation artifacts, and the organizations permitted for that query.
Scoring records Recall@k, MRR, nDCG@k, filter correctness, citation correctness, and cross-tenant
leakage rate. The frozen dataset hash is copied into the run and checked by PostgreSQL.

An evaluation run passes only when every configured threshold passes. A ready version cannot be
activated without a passing run. Activation retires the currently active sibling version in the
same transaction and stores the exact passing evaluation-run ID, so users never observe two active
versions for one logical index and can inspect the decision later.

## Management boundary

Index build payloads can reference private evidence across organizations and retrieval datasets
may deliberately name cross-tenant hard negatives. For that reason, configuration, build,
inactive preview, evaluation, dataset inspection, and activation use the system-only
`platform.search.manage` permission. Organization administrators cannot grant themselves this
permission. Bearer deployments issue a short-lived, auditable token for the reserved platform
operator; that principal can manage and preview indexes but cannot use the token to read private
artifacts through unrelated APIs. Normal evidence search uses the ordinary `search` permission and
the same backend scope policy as direct artifact and case access.
