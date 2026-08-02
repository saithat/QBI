ALTER TABLE annotation_revisions
    DROP CONSTRAINT IF EXISTS annotation_revision_structured_json_check;

ALTER TABLE annotation_revisions
    DROP COLUMN IF EXISTS structured_annotation_json;

DELETE FROM schema_migrations WHERE version = '0006_structured_annotations';
