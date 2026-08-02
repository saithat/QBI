# Evaluation metrics, calibration, and regression comparison

```text
content-addressed reference cases       versioned pipeline submission
  fields / regions / relations            predictions + confidences
  citations / numbers / rankings          publications + model versions
                  \                         /
                   strict case-set equality
                              |
                  deterministic scorer + config
                              |
             case results + groups + overall summary
                    |                    |
          confidence outcomes       immutable JSONB run
                    |                    |
     Brier / ECE / reliability      indexed run + case rows
     precision / risk / coverage          |
                    \                    /
                     comparison on same snapshot
                              |
             inspectable regressions and improvements
```

## Contract boundary

`EvaluationDatasetSnapshot` is a frozen reference collection whose SHA-256 is derived from its
ordered canonical case content. `PipelineEvaluationSubmission` carries one prediction document
and at least one producing publication UUID for every reference case. `EvaluationScoringInput`
rejects missing or additional cases before service logic runs.

Reference and predicted observations use stable JSON Pointer field paths, region keys, entity
relationship triples, numerical field paths, and query/item ranking keys. Predictions add optional
confidence. Evidence artifact UUIDs are compared per observation key for citation correctness and
counted per predicted observation for provenance completeness. The contracts are independent of
HTTP schemas and PostgreSQL rows.

## Scoring semantics

- **Fields:** exact match canonicalizes JSON syntax; normalized match additionally trims and
  collapses string whitespace and optionally case-folds strings. Both report precision, recall,
  and F1.
- **Geometry:** regions share a case ID and stable region key, and must agree on region type,
  source artifact, canvas, and page. Matching then applies the configured intersection-over-union
  threshold. Mean IoU and geometry precision/recall/F1 are separate from field scores.
- **Relationships:** exact subject, relation, and object triples report precision, recall, and F1.
- **Citations:** evidence artifact intersections report correctness, completeness, and F1 by
  stable observation key.
- **Provenance:** reports the fraction of predicted field, region, relationship, and numerical
  observations with at least one evidence artifact.
- **Numerical:** matching field paths with equal units report MAE, RMSE, mean relative error, and
  the fraction inside the configured absolute tolerance.
- **Ranking:** graded reference judgments produce Recall@k, MRR, and nDCG@k.

The composite is the arithmetic mean of represented categories: normalized-field F1, geometry F1,
relationship F1, citation F1, provenance completeness, numerical tolerance accuracy, and nDCG.
Categories absent on both sides are excluded. Scorer identity and the complete configuration make
that policy versionable.

## Calibration and grouping

Each predicted observation with confidence becomes an immutable correct/incorrect outcome under
the same scorer configuration. Reliability buckets include empty buckets so diagrams are stable.
Calibration includes Brier score, expected calibration error, and configured confidence threshold
points with retained count, coverage, precision, and risk. The API can recompute a slice for one
observation category from stored case outcomes without rescoring source documents.

Group summaries are computed over source type, journal, repository, image quality, assay layout,
review status, model version, and pipeline version when present. They use the exact same scorer and
configuration as the overall run.

## Persistence and inspection

Migration `0008_evaluation_metrics` adds immutable run and case-result tables. Full canonical JSON
is retained for reproducibility; relational columns support bounded filters by dataset and
pipeline. The API exposes run creation/history/detail, case detail, category calibration, and
same-snapshot comparison. `/metrics` visualizes stage scores, grouped slices, reliability,
abstention behavior, and links every changed case to `/workbench/<case-id>`.
