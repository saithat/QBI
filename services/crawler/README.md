# Public discovery service

`hiveblot_crawler` owns API-first source discovery, canonical URL/accession normalization, immutable
discovery-run provenance, and the durable crawl frontier. Exact official API responses are stored as
immutable artifacts before normalization. It does not acquire paper/source-data bytes or parse
papers in PRD-015; distributed fetching belongs to PRD-016.
