# Spatial annotation architecture

```text
prediction evidence regions                  reviewer head revision
          |                                           |
          v                                           v
   SpatialAnnotationSet <---- individual/bulk accept + immutable graph
          |                                           |
          `------------ semantic UUID diff -----------'
                              |
                     source-pixel SVG editor
                    /    |       |       \
             create   resize   split   relationships
                              |
                   expected head revision UUID
                              |
              EvaluationService.append_revision
                              |
       PostgreSQL spatial rows + typed relationship rows
```

The API composes prediction geometry, the selected reviewer's current graph, revision summaries,
error codes, and a semantic prediction comparison. Prediction regions are derived only from
`PredictionEvidence.region`; model output without explicit source geometry remains visible as an
unsupported prediction rather than becoming canonical spatial data.

The browser requests source metadata from the PRD-005 workbench, obtains an expiring download URL
only for the active source, and streams bytes directly from S3/MinIO. An SVG `viewBox` uses the
contract's `canvas_width` and `canvas_height`, so stored rectangles remain in source pixels while CSS
scales, rotates, and pans the visual. Each source retains its viewport while the reviewer switches
artifacts.

The graph encodes containment, labels, grouping, lane order, and target/loading-control links.
Containment connects compatible hierarchy levels on the same artifact/page/canvas and requires the
child rectangle to fit its parent. `precedes` edges form disjoint linear chains without forks,
multiple incoming edges, or cycles. Backend validation is authoritative; browser filters provide
earlier feedback but are not a security or integrity boundary.

Saving a spatial draft revalidates the complete strict graph and appends one immutable annotation
revision. The service partitions spatial edges by both vocabulary and endpoint membership, which
prevents the editor from dropping unrelated generic relationships. Restore is likewise
surface-scoped: historic geometry is copied into a new revision while current structured and field
annotations remain intact.

The persistence mapper prefixes spatial edge verbs with `spatial:` in the generic relationship
table, then removes the prefix when rebuilding the strict editor contract. Endpoint membership is
checked as a second discriminator. A historic generic `contains` edge can therefore coexist with a
spatial `contains` edge without being reinterpreted or dropped.
