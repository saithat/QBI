# Source evidence workbench architecture

```text
evaluation case
   |-- case/artifact/role association ---- latest caption/nearby-text revision
   |-- immutable artifact metadata ------- SHA-256, MIME, provenance, relationships
   |-- predictions ----------------------- optional source-pixel regions
   |-- reviewer document heads ----------- source-pixel regions + field links
   `-- adjudications --------------------- selected revision regions
                       |
              SourceEvidenceWorkbench
                       |
              strict HTTP response
                       |
       active source -> expiring URL -> S3/MinIO bytes
```

The API returns no signed URL in the aggregate response. This avoids recording access for sources
the reviewer never opens and prevents a large case from generating many unnecessary grants. The
browser calls the existing artifact download-URL endpoint when a source becomes active.

Overlay IDs are stable UUIDv5 values derived from their immutable producing record and spatial
annotation. `linked_field_keys` connect structured field paths or entity keys to every evidence
region. Selecting a field can therefore switch sources and highlight its linked regions without
changing an annotation.

The browser stores zoom, pan, rotation, and page state per artifact for the life of the workbench.
Image bytes stream directly into a progressively decoded image. PDFs use the browser's native
viewer; source-image overlays are drawn on image/page-render artifacts with an SVG source-pixel
view box.

Caption and nearby-text updates append immutable revisions. The workbench projects the latest
revision, while the revision-history endpoint preserves the full chain. Writers provide the
expected head revision so concurrent ingestion or correction fails with a conflict instead of
silently replacing text evidence.
