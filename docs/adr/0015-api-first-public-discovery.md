# ADR 0015: Discover public papers through source APIs before fetching bytes

## Status

Accepted.

## Decision

Separate public-record discovery from content acquisition. Source-specific adapters call official
APIs or manifests and emit strict `DiscoveryBatch` records; they do not publish artifacts or parse
papers. A source-independent frontier service normalizes HTTP(S) URLs, deduplicates stable source
identities and canonical URLs, preserves every discovery observation, records typed relationships,
and schedules later acquisition. PRD-016 workers will lease this frontier rather than rediscovering
work from Kubernetes objects.

Use PMC's official OAI-PMH endpoint as the first adapter. Query only the `pmc-open` set with
`ListIdentifiers`, build accession-based `GetRecord` URLs, publish every bounded raw response as an
immutable public source-adapter artifact, retain its hash and request URL as evidence, and paginate
with opaque resumption tokens. Open-set membership is not treated as
an article-level license grant: emitted license state remains `unverified` until a later acquisition
or metadata adapter verifies the article's terms. Deleted OAI records are explicit prohibited
frontier entries rather than silently disappearing.

Store discovery runs, immutable candidate JSON, response-artifact foreign keys, frontier aliases,
relationships, events, and artifact-acquisition links in PostgreSQL. Keep one canonical frontier
record per normalized URL and
stable identity while retaining every source observation and alias. Manual scheduling and retry use
optimistic integer versions. Acquired bytes remain immutable artifacts in the existing object-store
boundary; the frontier stores only references.

Run bounded scheduled discovery as a Kubernetes `CronJob` with `concurrencyPolicy: Forbid`, a hard
deadline, finite retries/history, explicit resources, a read-only root filesystem, and a tokenless
service account. The kind overlay suspends the CronJob so local cluster setup never contacts an
external scientific repository unexpectedly.

## Consequences

- Discovery can be replayed, inspected, delayed, or prioritized without downloading content.
- Scaling future fetch workers cannot accidentally multiply discovery calls or make Kubernetes the
  source of truth for crawl state.
- Stable source aliases and canonical URLs converge while all original candidate documents remain
  queryable in `discovery_runs` and `discovery_observations`.
- Source adapters are independently testable with recorded protocol fixtures and no live network.
- OAI resumption tokens are surfaced by the CLI but are not yet a durable scheduler cursor. A
  bounded run that returns `next_cursor` requires an explicit resumed invocation.
- Distributed fetching, robots evaluation for controlled crawling, shared domain rate limiting,
  parsing, dead-letter attempt transitions, and queue autoscaling remain PRD-016.

## Source protocol references

- [PMC Open Access Web Service](https://pmc.ncbi.nlm.nih.gov/tools/oa-service/)
- [PMC OAI-PMH API](https://pmc.ncbi.nlm.nih.gov/tools/oai/)
- [PMC cloud and bulk access](https://pmc.ncbi.nlm.nih.gov/tools/pmcaws/)
- [PMC copyright and automated retrieval guidance](https://pmc.ncbi.nlm.nih.gov/about/copyright/)
