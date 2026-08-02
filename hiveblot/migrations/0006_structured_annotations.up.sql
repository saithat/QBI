ALTER TABLE annotation_revisions
    ADD COLUMN structured_annotation_json JSONB;

ALTER TABLE annotation_revisions
    ADD CONSTRAINT annotation_revision_structured_json_check CHECK (
        structured_annotation_json IS NULL
        OR jsonb_typeof(structured_annotation_json) = 'object'
    );
