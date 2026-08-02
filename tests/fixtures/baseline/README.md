# Hackathon extraction baseline

This fixture is a captured, deterministic boundary from the pre-foundation pipeline. The
source PDF and candidate crop are identified by SHA-256 but are not committed. The committed
input is the raw page-4 model response; the committed output is the exact list produced by
`hiveblot.records.flatten_records` before restructuring.

The smoke test therefore needs no GPU, model server, database, or copyrighted source image.
It protects the useful normalization behavior while PRD-011 remains deliberately deferred.
