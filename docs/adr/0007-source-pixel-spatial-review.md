# ADR 0007: Store validated source-pixel graphs on immutable review revisions

## Status

Accepted.

## Decision

Represent one reviewer geometry snapshot as a strict `SpatialAnnotationSet`: existing source-pixel
`SpatialAnnotation` records plus typed `SpatialEditorRelationship` edges. Stable UUIDs identify
regions and relationships. The graph validates endpoint existence, relationship uniqueness,
compatible region types, acyclic containment, single-chain lane ordering, matching source
coordinate spaces, and geometric enclosure for `contains` edges.

Use the existing PRD-003 `spatial_annotations` and `annotation_relationships` tables. No database
migration is needed. The mapping service recognizes a relationship as spatial only when its type is
stored with the `spatial:` namespace and both endpoints are spatial annotations in that revision;
generic relationships with the same textual verb remain preserved.

Spatial saves append through `EvaluationService` with an expected head revision UUID. They carry
forward current field annotations, the typed western-blot snapshot, and non-spatial relationships.
Spatial restore copies only the selected historic geometry graph into a new revision while retaining
the current non-spatial review data. Predictions remain independent immutable documents, and
acceptance creates a draft before an explicit save.

## Consequences

- Browser zoom and resizing do not change stored coordinates; SVG overlays use each immutable
  source image's pixel coordinate system.
- Figure, panel, blot, lane, protein-row, band, label, and quantification-plot regions share one
  versioned public contract and committed JSON Schema.
- Lane order is scientific graph data, not array position. Semantic diffs key geometry by stable
  region UUID rather than tuple order.
- Invalid bounds, dangling endpoints, containment errors, forks, cycles, duplicate edges, and stale
  revisions fail before publication.
- PDF sources remain inspectable but read-only; reviewers edit an image or page-render artifact.
- Automatic region detection and deterministic measurement remain deferred to PRD-011 and PRD-012.
