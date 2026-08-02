ALTER TABLE artifacts
    DROP CONSTRAINT artifacts_acquisition_method_check;

ALTER TABLE artifacts
    ADD CONSTRAINT artifacts_acquisition_method_check CHECK (
        acquisition_method IN ('user_upload', 'source_adapter', 'tool_output')
    );

ALTER TABLE artifact_uploads
    DROP CONSTRAINT artifact_uploads_acquisition_method_check;

ALTER TABLE artifact_uploads
    ADD CONSTRAINT artifact_uploads_acquisition_method_check CHECK (
        acquisition_method IN ('user_upload', 'source_adapter', 'tool_output')
    );
