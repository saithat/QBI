DO $$
BEGIN
    IF EXISTS (
        SELECT case_id, reviewer_id
        FROM reviewer_assignments
        WHERE assignment_status = 'assigned'
        GROUP BY case_id, reviewer_id
        HAVING COUNT(*) > 1
    ) OR EXISTS (
        SELECT case_id
        FROM reviewer_assignments
        WHERE assignment_status = 'assigned' AND exclusive
        GROUP BY case_id
        HAVING COUNT(*) > 1
    ) THEN
        RAISE EXCEPTION USING
            MESSAGE = 'cannot roll back organization authorization while scoped reviewer assignments conflict',
            HINT = 'release duplicate active or exclusive assignments for each case before retrying';
    END IF;
END;
$$;

DROP TRIGGER pipeline_publication_outputs_scope ON pipeline_publication_output_artifacts;
DROP FUNCTION hiveblot_enforce_pipeline_publication_artifact_scope();
DROP TRIGGER pipeline_publication_scope ON pipeline_publications;
DROP FUNCTION hiveblot_enforce_pipeline_publication_scope();
DROP TRIGGER component_evidence_scope ON component_invocation_evidence_artifacts;
DROP TRIGGER component_outputs_scope ON component_invocation_output_artifacts;
DROP TRIGGER component_inputs_scope ON component_invocation_input_artifacts;
DROP FUNCTION hiveblot_enforce_component_artifact_scope();
DROP TRIGGER pipeline_run_inputs_scope ON pipeline_run_input_artifacts;
DROP FUNCTION hiveblot_enforce_pipeline_run_artifact_scope();
DROP FUNCTION hiveblot_artifact_matches_scope(TEXT, UUID, UUID, BOOLEAN);
DROP TRIGGER pipeline_run_scope ON pipeline_runs;
DROP FUNCTION hiveblot_enforce_pipeline_run_scope();
DROP TRIGGER job_outputs_scope ON job_outputs;
DROP FUNCTION hiveblot_enforce_job_output_scope();
DROP TRIGGER jobs_scope ON jobs;
DROP FUNCTION hiveblot_enforce_job_scope();
DROP TRIGGER adjudication_considered_revision_scope ON adjudication_considered_revisions;
DROP FUNCTION hiveblot_enforce_adjudication_considered_scope();
DROP TRIGGER adjudication_record_scope ON adjudication_records;
DROP FUNCTION hiveblot_enforce_adjudication_scope();
DROP TRIGGER reviewer_assignment_scope ON reviewer_assignments;
DROP FUNCTION hiveblot_enforce_reviewer_assignment_scope();
DROP TRIGGER annotation_document_scope ON annotation_documents;
DROP FUNCTION hiveblot_enforce_annotation_scope();
DROP TRIGGER artifact_relationship_scope ON artifact_relationships;
DROP FUNCTION hiveblot_enforce_artifact_relationship_scope();
DROP TRIGGER evaluation_case_artifact_scope ON evaluation_case_artifacts;
DROP FUNCTION hiveblot_enforce_case_artifact_scope();

ALTER TABLE jobs
    DROP CONSTRAINT jobs_visibility_scope_check,
    DROP CONSTRAINT jobs_submitted_by_fk,
    DROP CONSTRAINT jobs_organization_fk,
    DROP COLUMN submitted_by,
    DROP COLUMN organization_id,
    DROP COLUMN visibility;

UPDATE pipeline_publications
SET publication_json = publication_json - 'visibility' - 'organization_id';
ALTER TABLE pipeline_publications
    DROP CONSTRAINT pipeline_publications_visibility_scope_check,
    DROP CONSTRAINT pipeline_publications_organization_fk,
    DROP COLUMN organization_id,
    DROP COLUMN visibility;

UPDATE pipeline_runs
SET run_json = run_json - 'visibility' - 'organization_id';
ALTER TABLE pipeline_runs
    DROP CONSTRAINT pipeline_runs_visibility_scope_check,
    DROP CONSTRAINT pipeline_runs_organization_fk,
    DROP COLUMN organization_id,
    DROP COLUMN visibility;
ALTER TABLE adjudication_records
    DROP CONSTRAINT adjudication_records_visibility_scope_check,
    DROP CONSTRAINT adjudication_records_organization_fk,
    DROP COLUMN organization_id,
    DROP COLUMN visibility;
ALTER TABLE annotation_documents
    DROP CONSTRAINT annotation_documents_visibility_scope_check,
    DROP CONSTRAINT annotation_documents_organization_fk,
    DROP COLUMN organization_id,
    DROP COLUMN visibility;

DROP INDEX reviewer_assignments_exclusive_case_idx;
DROP INDEX reviewer_assignments_active_reviewer_idx;
ALTER TABLE reviewer_assignments
    DROP CONSTRAINT reviewer_assignments_visibility_scope_check,
    DROP CONSTRAINT reviewer_assignments_organization_fk,
    DROP COLUMN organization_id,
    DROP COLUMN visibility;
CREATE UNIQUE INDEX reviewer_assignments_active_reviewer_idx
    ON reviewer_assignments (case_id, reviewer_id)
    WHERE assignment_status = 'assigned';
CREATE UNIQUE INDEX reviewer_assignments_exclusive_case_idx
    ON reviewer_assignments (case_id)
    WHERE assignment_status = 'assigned' AND exclusive;
ALTER TABLE evaluation_cases
    DROP CONSTRAINT evaluation_cases_visibility_scope_check,
    DROP CONSTRAINT evaluation_cases_organization_fk,
    DROP COLUMN organization_id,
    DROP COLUMN visibility;
ALTER TABLE artifact_uploads DROP CONSTRAINT artifact_uploads_organization_fk;
ALTER TABLE artifacts DROP CONSTRAINT artifacts_organization_fk;

DROP TRIGGER authorization_audit_events_append_only ON authorization_audit_events;
DROP FUNCTION hiveblot_prevent_audit_mutation();
DROP TABLE authorization_audit_events;
DROP TABLE api_access_tokens;
DROP TABLE organization_memberships;
DROP TABLE organizations;
DROP TABLE auth_users;
DELETE FROM schema_migrations WHERE version = '0014_organization_authorization';
