# ADR 0012: Keep densitometry deterministic and geometry-revision bound

## Status

Accepted.

## Decision

Implement western-blot densitometry as a versioned deterministic tool, never as a model-generated
number. A run consumes one immutable raster artifact, its exact SHA-256, one explicit prediction or
reviewer geometry revision, lane/protein-row/band regions in native source-image pixels, a complete
configuration, and an optional loading-control target.

Version 1 calculates integrated darkness as `sum(255 - grayscale_pixel)` inside each band rectangle.
It subtracts either no background, a configured percentile over the lane/target intersection, or a
configured local border. Loading-control normalization divides each corrected target intensity by
the corrected control intensity from the same lane. The tool rounds public numerical values to six
decimal places and records its algorithm, tool, NumPy, and Pillow versions.

Publish the generated PNG analysis overlay as a content-addressed `tool_output` artifact with a
`derived_from` relationship to the source image. Record the calculation as the single
`densitometry_measurement` component of the versioned `western-blot-densitometry` pipeline. A replay
uses the stored exact input and appends a new invocation and publication; it never replaces the
original attempt.

Treat source suitability independently from numerical reproducibility. Raw source images can be
quantitative when no QC flags apply. Warnings reduce them to semi-quantitative. Publication figures
are always exploratory-only at best, and any error-level flag makes the result not analyzable.
Saturation, resolution, missing or zero controls, unreviewed lane boundaries, unknown exposure,
uneven background, and empty local background are explicit typed QC states.

## Consequences

- Models may suggest geometry or scientific interpretation but cannot create intensity values.
- Every measurement identifies the exact image hash, geometry revision, configuration hash, tool
  version, background estimate, corrected value, normalization, QC state, and producing invocation.
- Geometry correction remains an append-only spatial-review operation. Rerunning densitometry after
  a correction adds a new result while preserving prior measurements.
- Identical inputs and tool versions produce identical result identifiers and overlay bytes, while
  replays still receive distinct invocation, trace, and publication identifiers.
- Rectangular integrated darkness is deliberately narrower than full ImageJ-style analysis. Optical
  density calibration, curved regions, multi-exposure fusion, and automated claim generation require
  separately versioned tools and validation datasets.
