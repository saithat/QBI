# Immutable artifact storage

## Publication flow

1. The API creates an `artifact_uploads` row and an S3 multipart upload under `staging/`.
2. The client uploads parts using expiring S3 URLs and returns part numbers plus ETags.
3. HiveBlot completes the staging object, streams it once to calculate SHA-256 and byte size, and
   detects its media type from bytes rather than filename.
4. Validated identity is persisted on the upload session so publication can be retried safely.
5. Bytes are copied to `artifacts/sha256/<prefix>/<sha256>` and the staging object is removed.
6. One visibility-scoped artifact record and its provenance event are committed in PostgreSQL.

Source adapters enter at step 3 after writing their allowlisted response to a staging object. The
HTTP adapter follows a bounded number of redirects, validates every redirect host against an exact
allowlist, and enforces streamed byte limits.

Adapters that already hold a bounded response may publish those exact bytes through
`publish_source_payload`; this avoids a second request while preserving the same staging, media,
hash, deduplication, event, and immutable-publication boundary. Public discovery uses this path for
official API responses before XML normalization.

## Integrity boundaries

- Object keys are derived internally from hashes.
- Content length, detected media type, declared media type, expected length, and stored metadata
  must agree before an artifact is returned.
- Unsupported binary data and mismatched declared types fail the upload without creating an
  artifact.
- Public rows reject organization IDs; organization-private rows require them in Pydantic and SQL.
- Relationship rows use foreign keys and cannot self-reference.
- Creation, deduplication, aborted/failed upload, and signed-URL access events are append-only.

## Supported content

The v1 sniffer accepts PDF, PNG, JPEG, GIF, TIFF, WebP, ZIP, gzip, tar, JSON, XML, CSV, and TSV. A file
extension does not affect detection. Additional formats should be added through versioned,
fixture-backed detectors rather than trusting caller headers.
