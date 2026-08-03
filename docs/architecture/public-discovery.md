# Public paper discovery and crawl frontier

```text
official API / bulk manifest / accession source
                    |
        permissive source adapter
 raw response artifact + request provenance
                    |
       strict DiscoveryBatch v1.0
                    |
 URL/accession normalization + validation
                    |
        PostgreSQL crawl frontier
       /             |              \
 aliases      typed relationships   events
                    |
       durable fetch-task lease
                    |
 shared domain permit + HTTP policy
                    |
 immutable artifact -> acquisition + parse outbox
```

Discovery says that a source record exists and may be eligible for acquisition. It does not claim
that bytes were downloaded, a license was verified, or a paper was parsed. This boundary lets the
platform inspect and revise scheduling decisions before network or compute work begins.

## Canonical contracts

`DiscoveryRecord` contains a stable source identity, source record ID/accession, entity kind,
canonical URL, acquisition method, timing/priority, expected media types, robots state, access and
license state, relationships, and trace ID. `DiscoveryBatch` adds source tool identity, query,
response evidence, validation issues, timestamps, and an optional continuation cursor. Every
evidence entry references an immutable artifact whose hash/media metadata must match the exact raw
HTTP response. Unknown
fields and coercion fail before persistence.

The supported entity kinds are paper, supplementary archive, source-data image, and metadata
record. Relationships describe paper/supplement/source-data associations, metadata descriptions,
and alternate representations. The PMC OAI adapter currently emits paper and front-matter metadata
records; later official adapters may emit the other relationship types without changing frontier
storage.

`CrawlFrontierRecord` is a distinct persistence/API contract. It adds the canonical frontier ID,
status, attempt count, optional lease, version, discovery history pointer, relationships, and all
artifact acquisitions. It is not reused as a queue message or ORM entity.

## Deduplication and history

The database enforces unique canonical URLs and primary identity keys. `crawl_frontier_aliases`
maps every observed stable identity to one canonical record. Ingestion locks all matches for an
identity, alias, or URL; resolving those keys to different records fails rather than silently
merging scientific provenance.

`discovery_runs` stores the entire normalized batch and a deterministic SHA-256. Reusing a batch ID
with identical content is idempotent; reusing it with different content is a conflict.
`discovery_observations` preserves each original candidate JSON and its source batch even after
frontier metadata evolves. Rediscovery appends an event and advances the version without deleting
history. A verified license or explicit prohibition is not downgraded by weaker rediscovery data.

## Scheduling and state

Initial state is derived explicitly:

- prohibited access becomes `prohibited`;
- an otherwise eligible record with expected media types becomes `pending`; and
- a record with no supported expected media type becomes `unsupported`.

The public inspection API filters by status, entity kind, source, access status, and whether an
artifact is missing. Results sort by descending priority, next eligible fetch time, and stable ID.
Schedule and manual-retry mutations require the current version and return `409` when stale.

PRD-016 maps eligible frontier records into durable fetch tasks. Long-lived workers lease those
tasks with `FOR UPDATE SKIP LOCKED`; an expired lease becomes retryable or dead-lettered without
losing its attempt history. The frontier mirrors active lease/status state while fetch tasks and
attempts retain execution-specific provenance.

## PMC adapter

`PmcOaiDiscoveryAdapter` calls the official OAI-PMH API with an identifying `User-Agent`, no
redirect following, bounded time/response size, explicit XML media types, and safe XML parsing that
rejects document type/entity declarations. It requests `ListIdentifiers` for `pmc-open`, follows
opaque `resumptionToken` values, and records the exact request URL, response artifact/hash, HTTP
status, media type, and fetch timestamp. Responses are streamed through a decompressed-byte limit,
published before XML normalization, and foreign-keyed to the discovery run.

For each non-deleted PMCID it emits a full-record `metadataPrefix=pmc` paper URL and a linked
`metadataPrefix=pmc_fm` metadata URL. Open-set membership remains an unverified license signal.
Deleted records become prohibited entries with no expected downloadable media type.

## Artifact traceability

Acquisition publication verifies the referenced artifact's ID, SHA-256, media type, and byte size
against artifact storage before linking it. Multiple acquisitions may be preserved for one frontier
record. Publishing the same artifact twice is idempotent, and a successful link advances the
frontier to `acquired`. HTTP transfer is owned by the distributed-fetch subsystem described in
`docs/architecture/distributed-fetching.md`.

The scheduled worker uses a dedicated discovery settings model and receives only database and
object-store credentials plus non-secret discovery configuration. It does not receive model,
Temporal, or Kubernetes API credentials.
