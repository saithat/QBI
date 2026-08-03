# HiveBlot evidence search service

`hiveblot_search` owns versioned evidence-index builds, lexical/vector retrieval, optional
reranking, frozen retrieval evaluation, and activation gates. It accepts explicit reviewed
observations and claims through strict contracts; it does not generate scientific claims.

PostgreSQL applies visibility and organization predicates before candidate rows leave persistence.
The service then checks the same scope defensively before scoring or returning a hit. Index
versions remain inactive until a frozen retrieval dataset produces a passing evaluation run.
