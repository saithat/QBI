DO $$
BEGIN
    IF EXISTS (
        SELECT 1 FROM artifacts WHERE acquisition_method = 'tool_output'
    ) OR EXISTS (
        SELECT 1 FROM artifact_uploads WHERE acquisition_method = 'tool_output'
    ) THEN
        RAISE EXCEPTION
            'cannot remove tool_output acquisition method while tool-output records exist';
    END IF;
END
$$;

ALTER TABLE artifacts
    DROP CONSTRAINT artifacts_acquisition_method_check;

ALTER TABLE artifacts
    ADD CONSTRAINT artifacts_acquisition_method_check CHECK (
        acquisition_method IN ('user_upload', 'source_adapter')
    );

ALTER TABLE artifact_uploads
    DROP CONSTRAINT artifact_uploads_acquisition_method_check;

ALTER TABLE artifact_uploads
    ADD CONSTRAINT artifact_uploads_acquisition_method_check CHECK (
        acquisition_method IN ('user_upload', 'source_adapter')
    );
