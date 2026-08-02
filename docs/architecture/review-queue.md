# Review queue architecture

```text
URL filters / saved view
        |
strict HTTP adapter
        |
ReviewQueueFilters contract
        |
ReviewQueueService
        |
PostgreSQL read projection ---- evaluation cases, latest prediction,
        |                        active assignments, head errors, source artifact
bounded ReviewQueuePage
        |
browser cards + lazy signed thumbnails
```

The query orders by case update time and stable case ID, returns an exact count, and caps pages at
200 records. It supports review state, dataset, assay, source, prediction version, confidence,
error category, reviewer, provenance, warnings, gold eligibility, disagreement, and regression
filters. Latest-prediction and reviewer fields are display projections; underlying immutable
documents remain the source of truth.

`evaluation_case_review_metadata` stores only queue-facing status that has no owner in earlier
PRDs. It defaults safely for every case. `saved_review_views` stores validated canonical filters,
owner UUID, timestamps, and an optimistic version.

Assignment is not duplicated in this package. The browser posts to PRD-003's assignment API, whose
PostgreSQL transaction locks the case before checking active exclusive assignments. A racing claim
therefore returns `409` rather than silently replacing an assignment.
