# HiveBlot evaluation package

`hiveblot_evaluation` owns evaluation-case, prediction, annotation-revision, assignment, and
adjudication services. It is assay-aware only through versioned contracts and does not import API
entry points. The review-queue repository provides indexed, server-side filtering and saved views;
it does not own browser rendering. Metric scoring begins in PRD-009.

`EvidenceWorkbenchService` composes artifact metadata, source context, and spatial evidence from
immutable prediction, reviewer-head, and adjudication records. It never publishes or proxies bytes.
