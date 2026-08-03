# Public discovery service

`hiveblot_crawler` owns API-first source discovery, canonical URL/accession normalization, immutable
discovery-run provenance, the durable crawl frontier, and queue-backed public-source fetching.
Exact official API responses and successfully fetched source bytes are stored as immutable
artifacts before normalization or downstream parsing. PostgreSQL coordinates worker leases,
per-domain request permits, robots state, retries, dead letters, and the parse-task outbox.
