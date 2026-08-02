# Deterministic western-blot densitometry

```text
immutable raster artifact + SHA-256
              |
exact prediction or reviewer geometry revision
  lanes + protein rows + one band per lane/target pair
              |
strict DensitometryInput + explicit configuration
              |
verified artifact bytes (size + SHA-256)
              |
integrated darkness -> background subtraction -> control normalization
              |
typed measurements + suitability + QC + reproducibility metadata
              |
content-addressed PNG overlay + immutable pipeline invocation/publication
              |
review workbench, exact replay, or geometry revision followed by a new run
```

`hiveblot_densitometry` owns scientific geometry resolution, pixel measurement, QC classification,
and publication through the existing pipeline registry. It does not own reviewer mutations, HTTP
transport, object-store persistence, model inference, or generic container execution. Canonical
process contracts live in `hiveblot_contracts`; HTTP-only adapters live in `apps/api`; immutable
bytes remain behind `hiveblot_storage`.

## Input boundary

`DensitometryInput` is strict, frozen, versioned, and complete. It requires:

- one immutable image artifact reference including SHA-256, media type, and byte size;
- `raw_source` or `publication_figure` source classification;
- an exact prediction ID or annotation-revision ID;
- unique ordered lanes and unique protein-row targets;
- exactly one band rectangle for every lane/target pair;
- one shared native image coordinate space with no PDF page coordinates;
- band containment within both its lane and protein row; and
- an explicit background and normalization configuration.

The geometry resolver reads extraction predictions and every immutable reviewer revision. It only
offers snapshots that form a complete analyzable grid on a case image. Loading controls are inferred
from validated `target_uses_loading_control` relationships but remain explicit in the final input.
The spatial editor is the only geometry-mutation surface; its saves create new reviewer revisions.

## Numerical algorithm

The v1 tool converts the decoded raster to 8-bit grayscale and defines darkness as `255 - pixel`.
For every band rectangle it records pixel count and raw integrated darkness. Background is one of:

- `none`: zero background;
- `global_percentile`: the configured darkness percentile over unmasked pixels in the intersection
  of that lane and protein row; or
- `local_border`: the percentile within a clipped border around the band, with an explicit warning
  and deterministic global fallback if the border has no samples.

The background estimate is background-per-pixel multiplied by the band pixel count. Corrected
intensity is `max(0, raw - background)`. Loading-control normalization divides by the corrected
control for the same lane. A missing or zero control yields no invented normalized value and an
error-level QC flag. Public values are rounded to six decimal places.

## Quality and suitability

QC runs on the exact pixels and declared review assumptions. Band-level checks include minimum
dimensions/area, saturation, local-background availability, and background variation. Global checks
include publication-image compression risk, unknown exposure, unreviewed lane boundaries, and
loading-control availability.

Suitability is deterministic:

- any error flag: `not_analyzable`;
- otherwise a publication figure: `exploratory_only`;
- otherwise any warning: `semi_quantitative`;
- otherwise: `quantitative`.

This classification is a quality statement, not evidence that the underlying experiment supports a
scientific claim.

## Provenance, persistence, and replay

The pipeline registry stores raw component JSON separately from the normalized
`DensitometryResult`. The result identifies the exact input, source hash, configuration hash,
algorithm/tool/library versions, per-band values, QC flags, and immutable overlay artifact. Evidence
references point from each input band to its source-pixel rectangle. Cost is recorded as zero because
the path invokes no model or paid service; latency and trace IDs remain explicit.

A fresh run creates a new one-component pipeline run. Replay reconstructs the strict input from the
stored run configuration and creates a new attempt and publication. Deterministic result IDs and
overlay content may deduplicate, but invocation, trace, and publication history always append.
