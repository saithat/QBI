CREATE TABLE auth_users (
    user_id UUID PRIMARY KEY,
    email TEXT NOT NULL CHECK (char_length(btrim(email)) BETWEEN 3 AND 320),
    display_name TEXT NOT NULL CHECK (char_length(btrim(display_name)) BETWEEN 1 AND 200),
    user_status TEXT NOT NULL CHECK (user_status IN ('active', 'disabled')),
    created_at TIMESTAMPTZ NOT NULL,
    updated_at TIMESTAMPTZ NOT NULL CHECK (updated_at >= created_at)
);

CREATE UNIQUE INDEX auth_users_email_idx ON auth_users (lower(email));

CREATE TABLE organizations (
    organization_id UUID PRIMARY KEY,
    slug TEXT NOT NULL CHECK (slug ~ '^[a-z0-9][a-z0-9_-]{0,199}$'),
    display_name TEXT NOT NULL CHECK (char_length(btrim(display_name)) BETWEEN 1 AND 200),
    organization_status TEXT NOT NULL CHECK (organization_status IN ('active', 'suspended')),
    created_at TIMESTAMPTZ NOT NULL,
    updated_at TIMESTAMPTZ NOT NULL CHECK (updated_at >= created_at),
    UNIQUE (slug)
);

WITH legacy_organizations AS (
    SELECT organization_id FROM artifacts WHERE organization_id IS NOT NULL
    UNION
    SELECT organization_id FROM artifact_uploads WHERE organization_id IS NOT NULL
)
INSERT INTO organizations (
    organization_id, slug, display_name, organization_status, created_at, updated_at
)
SELECT
    organization_id,
    'legacy-' || replace(organization_id::text, '-', ''),
    'Migrated organization ' || organization_id::text,
    'active',
    transaction_timestamp(),
    transaction_timestamp()
FROM legacy_organizations
ON CONFLICT (organization_id) DO NOTHING;

CREATE TABLE organization_memberships (
    membership_id UUID PRIMARY KEY,
    organization_id UUID NOT NULL REFERENCES organizations(organization_id) ON DELETE RESTRICT,
    user_id UUID NOT NULL REFERENCES auth_users(user_id) ON DELETE RESTRICT,
    role TEXT NOT NULL CHECK (
        role IN ('organization_administrator', 'scientist', 'reviewer', 'read_only')
    ),
    active BOOLEAN NOT NULL,
    created_at TIMESTAMPTZ NOT NULL,
    updated_at TIMESTAMPTZ NOT NULL CHECK (updated_at >= created_at),
    UNIQUE (organization_id, user_id)
);

CREATE INDEX organization_memberships_user_idx
    ON organization_memberships (user_id, active, organization_id);

CREATE TABLE api_access_tokens (
    token_id UUID PRIMARY KEY,
    user_id UUID NOT NULL REFERENCES auth_users(user_id) ON DELETE RESTRICT,
    token_digest CHAR(64) NOT NULL UNIQUE CHECK (token_digest ~ '^[a-f0-9]{64}$'),
    label TEXT NOT NULL CHECK (char_length(btrim(label)) BETWEEN 1 AND 200),
    created_at TIMESTAMPTZ NOT NULL,
    expires_at TIMESTAMPTZ NOT NULL CHECK (expires_at > created_at),
    last_used_at TIMESTAMPTZ,
    revoked_at TIMESTAMPTZ CHECK (revoked_at IS NULL OR revoked_at >= created_at)
);

CREATE INDEX api_access_tokens_user_idx
    ON api_access_tokens (user_id, expires_at DESC, token_id);

CREATE TABLE authorization_audit_events (
    audit_event_id UUID PRIMARY KEY,
    actor_user_id UUID NOT NULL REFERENCES auth_users(user_id) ON DELETE RESTRICT,
    token_id UUID REFERENCES api_access_tokens(token_id) ON DELETE RESTRICT,
    action TEXT NOT NULL CHECK (char_length(btrim(action)) BETWEEN 1 AND 200),
    outcome TEXT NOT NULL CHECK (outcome IN ('allowed', 'denied')),
    target_type TEXT NOT NULL CHECK (char_length(btrim(target_type)) BETWEEN 1 AND 200),
    target_id UUID,
    organization_id UUID REFERENCES organizations(organization_id) ON DELETE RESTRICT,
    request_id UUID NOT NULL,
    reason TEXT CHECK (reason IS NULL OR char_length(reason) BETWEEN 1 AND 1000),
    occurred_at TIMESTAMPTZ NOT NULL
);

CREATE INDEX authorization_audit_organization_idx
    ON authorization_audit_events (organization_id, occurred_at DESC, audit_event_id DESC);
CREATE INDEX authorization_audit_actor_idx
    ON authorization_audit_events (actor_user_id, occurred_at DESC, audit_event_id DESC);
CREATE INDEX authorization_audit_target_idx
    ON authorization_audit_events (target_type, target_id, occurred_at DESC);

CREATE FUNCTION hiveblot_prevent_audit_mutation() RETURNS trigger
LANGUAGE plpgsql AS $$
BEGIN
    RAISE EXCEPTION 'authorization audit events are append-only';
END;
$$;

CREATE TRIGGER authorization_audit_events_append_only
    BEFORE UPDATE OR DELETE ON authorization_audit_events
    FOR EACH ROW EXECUTE FUNCTION hiveblot_prevent_audit_mutation();

ALTER TABLE artifacts
    ADD CONSTRAINT artifacts_organization_fk
    FOREIGN KEY (organization_id) REFERENCES organizations(organization_id)
    ON DELETE RESTRICT NOT VALID;
ALTER TABLE artifact_uploads
    ADD CONSTRAINT artifact_uploads_organization_fk
    FOREIGN KEY (organization_id) REFERENCES organizations(organization_id)
    ON DELETE RESTRICT NOT VALID;

ALTER TABLE evaluation_cases
    ADD COLUMN visibility TEXT NOT NULL DEFAULT 'public' CHECK (
        visibility IN ('public', 'organization_private')
    ),
    ADD COLUMN organization_id UUID;

DO $$
BEGIN
    IF EXISTS (
        SELECT source.case_id
        FROM evaluation_case_artifacts AS source
        JOIN artifacts AS artifact ON artifact.artifact_id = source.artifact_id
        WHERE artifact.visibility = 'organization_private'
        GROUP BY source.case_id
        HAVING COUNT(DISTINCT artifact.organization_id) > 1
    ) THEN
        RAISE EXCEPTION 'evaluation case references private artifacts from multiple organizations';
    END IF;
END;
$$;

UPDATE evaluation_cases AS evaluation_case
SET visibility = 'organization_private', organization_id = private_scope.organization_id
FROM (
    SELECT source.case_id, MIN(artifact.organization_id::text)::uuid AS organization_id
    FROM evaluation_case_artifacts AS source
    JOIN artifacts AS artifact ON artifact.artifact_id = source.artifact_id
    WHERE artifact.visibility = 'organization_private'
    GROUP BY source.case_id
) AS private_scope
WHERE evaluation_case.case_id = private_scope.case_id;

ALTER TABLE evaluation_cases
    ADD CONSTRAINT evaluation_cases_organization_fk
    FOREIGN KEY (organization_id) REFERENCES organizations(organization_id)
    ON DELETE RESTRICT NOT VALID,
    ADD CONSTRAINT evaluation_cases_visibility_scope_check CHECK (
        (visibility = 'public' AND organization_id IS NULL)
        OR (visibility = 'organization_private' AND organization_id IS NOT NULL)
    );

ALTER TABLE annotation_documents
    ADD COLUMN visibility TEXT NOT NULL DEFAULT 'public' CHECK (
        visibility IN ('public', 'organization_private')
    ),
    ADD COLUMN organization_id UUID;

UPDATE annotation_documents AS annotation
SET visibility = evaluation_case.visibility,
    organization_id = evaluation_case.organization_id
FROM evaluation_cases AS evaluation_case
WHERE evaluation_case.case_id = annotation.case_id;

ALTER TABLE annotation_documents
    ADD CONSTRAINT annotation_documents_organization_fk
    FOREIGN KEY (organization_id) REFERENCES organizations(organization_id)
    ON DELETE RESTRICT NOT VALID,
    ADD CONSTRAINT annotation_documents_visibility_scope_check CHECK (
        (visibility = 'public' AND organization_id IS NULL)
        OR (visibility = 'organization_private' AND organization_id IS NOT NULL)
    );

ALTER TABLE reviewer_assignments
    ADD COLUMN visibility TEXT NOT NULL DEFAULT 'public' CHECK (
        visibility IN ('public', 'organization_private')
    ),
    ADD COLUMN organization_id UUID;

UPDATE reviewer_assignments AS assignment
SET visibility = evaluation_case.visibility,
    organization_id = evaluation_case.organization_id
FROM evaluation_cases AS evaluation_case
WHERE evaluation_case.case_id = assignment.case_id;

DROP INDEX reviewer_assignments_active_reviewer_idx;
DROP INDEX reviewer_assignments_exclusive_case_idx;

ALTER TABLE reviewer_assignments
    ADD CONSTRAINT reviewer_assignments_organization_fk
    FOREIGN KEY (organization_id) REFERENCES organizations(organization_id)
    ON DELETE RESTRICT NOT VALID,
    ADD CONSTRAINT reviewer_assignments_visibility_scope_check CHECK (
        (visibility = 'public' AND organization_id IS NULL)
        OR (visibility = 'organization_private' AND organization_id IS NOT NULL)
    );

CREATE UNIQUE INDEX reviewer_assignments_active_reviewer_idx
    ON reviewer_assignments (
        case_id,
        reviewer_id,
        visibility,
        COALESCE(organization_id, '00000000-0000-0000-0000-000000000000'::uuid)
    )
    WHERE assignment_status = 'assigned';

CREATE UNIQUE INDEX reviewer_assignments_exclusive_case_idx
    ON reviewer_assignments (
        case_id,
        visibility,
        COALESCE(organization_id, '00000000-0000-0000-0000-000000000000'::uuid)
    )
    WHERE assignment_status = 'assigned' AND exclusive;

ALTER TABLE adjudication_records
    ADD COLUMN visibility TEXT NOT NULL DEFAULT 'public' CHECK (
        visibility IN ('public', 'organization_private')
    ),
    ADD COLUMN organization_id UUID;

UPDATE adjudication_records AS adjudication
SET visibility = evaluation_case.visibility,
    organization_id = evaluation_case.organization_id
FROM evaluation_cases AS evaluation_case
WHERE evaluation_case.case_id = adjudication.case_id;

ALTER TABLE adjudication_records
    ADD CONSTRAINT adjudication_records_organization_fk
    FOREIGN KEY (organization_id) REFERENCES organizations(organization_id)
    ON DELETE RESTRICT NOT VALID,
    ADD CONSTRAINT adjudication_records_visibility_scope_check CHECK (
        (visibility = 'public' AND organization_id IS NULL)
        OR (visibility = 'organization_private' AND organization_id IS NOT NULL)
    );

ALTER TABLE pipeline_runs
    ADD COLUMN visibility TEXT NOT NULL DEFAULT 'public' CHECK (
        visibility IN ('public', 'organization_private')
    ),
    ADD COLUMN organization_id UUID;

UPDATE pipeline_runs AS run
SET visibility = evaluation_case.visibility,
    organization_id = evaluation_case.organization_id
FROM evaluation_cases AS evaluation_case
WHERE evaluation_case.case_id = run.case_id;

UPDATE pipeline_runs
SET run_json = run_json || jsonb_build_object(
    'visibility', visibility,
    'organization_id', organization_id
);

ALTER TABLE pipeline_runs
    ADD CONSTRAINT pipeline_runs_organization_fk
    FOREIGN KEY (organization_id) REFERENCES organizations(organization_id)
    ON DELETE RESTRICT NOT VALID,
    ADD CONSTRAINT pipeline_runs_visibility_scope_check CHECK (
        (visibility = 'public' AND organization_id IS NULL)
        OR (visibility = 'organization_private' AND organization_id IS NOT NULL)
    );

ALTER TABLE pipeline_publications
    ADD COLUMN visibility TEXT NOT NULL DEFAULT 'public' CHECK (
        visibility IN ('public', 'organization_private')
    ),
    ADD COLUMN organization_id UUID;

UPDATE pipeline_publications AS publication
SET visibility = run.visibility,
    organization_id = run.organization_id
FROM pipeline_runs AS run
WHERE run.run_id = publication.run_id;

UPDATE pipeline_publications
SET publication_json = publication_json || jsonb_build_object(
    'visibility', visibility,
    'organization_id', organization_id
);

ALTER TABLE pipeline_publications
    ADD CONSTRAINT pipeline_publications_organization_fk
    FOREIGN KEY (organization_id) REFERENCES organizations(organization_id)
    ON DELETE RESTRICT NOT VALID,
    ADD CONSTRAINT pipeline_publications_visibility_scope_check CHECK (
        (visibility = 'public' AND organization_id IS NULL)
        OR (visibility = 'organization_private' AND organization_id IS NOT NULL)
    );

ALTER TABLE jobs
    ADD COLUMN visibility TEXT NOT NULL DEFAULT 'public' CHECK (
        visibility IN ('public', 'organization_private')
    ),
    ADD COLUMN organization_id UUID,
    ADD COLUMN submitted_by UUID;

ALTER TABLE jobs
    ADD CONSTRAINT jobs_organization_fk
    FOREIGN KEY (organization_id) REFERENCES organizations(organization_id)
    ON DELETE RESTRICT NOT VALID,
    ADD CONSTRAINT jobs_submitted_by_fk
    FOREIGN KEY (submitted_by) REFERENCES auth_users(user_id)
    ON DELETE RESTRICT NOT VALID,
    ADD CONSTRAINT jobs_visibility_scope_check CHECK (
        (visibility = 'public' AND organization_id IS NULL)
        OR (visibility = 'organization_private' AND organization_id IS NOT NULL)
    );

CREATE FUNCTION hiveblot_enforce_case_artifact_scope() RETURNS trigger
LANGUAGE plpgsql AS $$
DECLARE
    case_visibility TEXT;
    case_organization UUID;
    artifact_visibility TEXT;
    artifact_organization UUID;
BEGIN
    SELECT visibility, organization_id INTO case_visibility, case_organization
    FROM evaluation_cases WHERE case_id = NEW.case_id;
    SELECT visibility, organization_id INTO artifact_visibility, artifact_organization
    FROM artifacts WHERE artifact_id = NEW.artifact_id;
    IF case_visibility = 'public' AND artifact_visibility <> 'public' THEN
        RAISE EXCEPTION 'public evaluation cases may reference only public artifacts';
    END IF;
    IF artifact_visibility = 'organization_private'
       AND artifact_organization IS DISTINCT FROM case_organization THEN
        RAISE EXCEPTION 'private case artifacts must belong to the case organization';
    END IF;
    RETURN NEW;
END;
$$;

CREATE TRIGGER evaluation_case_artifact_scope
    BEFORE INSERT OR UPDATE ON evaluation_case_artifacts
    FOR EACH ROW EXECUTE FUNCTION hiveblot_enforce_case_artifact_scope();

CREATE FUNCTION hiveblot_enforce_artifact_relationship_scope() RETURNS trigger
LANGUAGE plpgsql AS $$
DECLARE
    artifact_visibility TEXT;
    artifact_organization UUID;
    related_visibility TEXT;
    related_organization UUID;
BEGIN
    SELECT visibility, organization_id INTO artifact_visibility, artifact_organization
    FROM artifacts WHERE artifact_id = NEW.artifact_id;
    SELECT visibility, organization_id INTO related_visibility, related_organization
    FROM artifacts WHERE artifact_id = NEW.related_artifact_id;
    IF artifact_visibility = 'public' AND related_visibility <> 'public' THEN
        RAISE EXCEPTION 'public artifacts cannot reference private artifacts';
    END IF;
    IF related_visibility = 'organization_private'
       AND related_organization IS DISTINCT FROM artifact_organization THEN
        RAISE EXCEPTION 'private related artifacts must belong to the artifact organization';
    END IF;
    RETURN NEW;
END;
$$;

CREATE TRIGGER artifact_relationship_scope
    BEFORE INSERT OR UPDATE ON artifact_relationships
    FOR EACH ROW EXECUTE FUNCTION hiveblot_enforce_artifact_relationship_scope();

CREATE FUNCTION hiveblot_enforce_annotation_scope() RETURNS trigger
LANGUAGE plpgsql AS $$
DECLARE
    case_visibility TEXT;
    case_organization UUID;
BEGIN
    SELECT visibility, organization_id INTO case_visibility, case_organization
    FROM evaluation_cases WHERE case_id = NEW.case_id;
    IF case_visibility = 'organization_private'
       AND (NEW.visibility <> 'organization_private'
            OR NEW.organization_id IS DISTINCT FROM case_organization) THEN
        RAISE EXCEPTION 'private cases require annotations in the same organization';
    END IF;
    RETURN NEW;
END;
$$;

CREATE TRIGGER annotation_document_scope
    BEFORE INSERT OR UPDATE OF case_id, visibility, organization_id ON annotation_documents
    FOR EACH ROW EXECUTE FUNCTION hiveblot_enforce_annotation_scope();

CREATE FUNCTION hiveblot_enforce_reviewer_assignment_scope() RETURNS trigger
LANGUAGE plpgsql AS $$
DECLARE
    case_visibility TEXT;
    case_organization UUID;
BEGIN
    SELECT visibility, organization_id INTO case_visibility, case_organization
    FROM evaluation_cases WHERE case_id = NEW.case_id;
    IF case_visibility = 'organization_private'
       AND (NEW.visibility <> 'organization_private'
            OR NEW.organization_id IS DISTINCT FROM case_organization) THEN
        RAISE EXCEPTION 'private cases require reviewer assignments in the same organization';
    END IF;
    IF NEW.visibility = 'organization_private'
       AND NEW.assignment_status = 'assigned'
       AND NOT EXISTS (
           SELECT 1
           FROM organization_memberships AS membership
           WHERE membership.organization_id = NEW.organization_id
             AND membership.user_id = NEW.reviewer_id
             AND membership.active = TRUE
       ) THEN
        RAISE EXCEPTION 'private reviewer assignments require active organization membership';
    END IF;
    RETURN NEW;
END;
$$;

CREATE TRIGGER reviewer_assignment_scope
    BEFORE INSERT OR UPDATE OF
        case_id, reviewer_id, assignment_status, visibility, organization_id
    ON reviewer_assignments
    FOR EACH ROW EXECUTE FUNCTION hiveblot_enforce_reviewer_assignment_scope();

CREATE FUNCTION hiveblot_enforce_adjudication_scope() RETURNS trigger
LANGUAGE plpgsql AS $$
DECLARE
    case_visibility TEXT;
    case_organization UUID;
    revision_visibility TEXT;
    revision_organization UUID;
BEGIN
    SELECT visibility, organization_id INTO case_visibility, case_organization
    FROM evaluation_cases WHERE case_id = NEW.case_id;
    SELECT annotation.visibility, annotation.organization_id
        INTO revision_visibility, revision_organization
    FROM annotation_revisions AS revision
    JOIN annotation_documents AS annotation ON annotation.annotation_id = revision.annotation_id
    WHERE revision.revision_id = NEW.selected_revision_id;
    IF case_visibility = 'organization_private'
       AND (NEW.visibility <> 'organization_private'
            OR NEW.organization_id IS DISTINCT FROM case_organization) THEN
        RAISE EXCEPTION 'private cases require adjudication in the same organization';
    END IF;
    IF revision_visibility IS DISTINCT FROM NEW.visibility
       OR revision_organization IS DISTINCT FROM NEW.organization_id THEN
        RAISE EXCEPTION 'adjudication scope must match its selected annotation revision';
    END IF;
    RETURN NEW;
END;
$$;

CREATE CONSTRAINT TRIGGER adjudication_record_scope
    AFTER INSERT OR UPDATE OF case_id, selected_revision_id, visibility, organization_id
    ON adjudication_records DEFERRABLE INITIALLY DEFERRED
    FOR EACH ROW EXECUTE FUNCTION hiveblot_enforce_adjudication_scope();

CREATE FUNCTION hiveblot_enforce_adjudication_considered_scope() RETURNS trigger
LANGUAGE plpgsql AS $$
DECLARE
    adjudication_case UUID;
    adjudication_visibility TEXT;
    adjudication_organization UUID;
    revision_case UUID;
    revision_visibility TEXT;
    revision_organization UUID;
BEGIN
    SELECT case_id, visibility, organization_id
        INTO adjudication_case, adjudication_visibility, adjudication_organization
    FROM adjudication_records WHERE adjudication_id = NEW.adjudication_id;
    SELECT annotation.case_id, annotation.visibility, annotation.organization_id
        INTO revision_case, revision_visibility, revision_organization
    FROM annotation_revisions AS revision
    JOIN annotation_documents AS annotation ON annotation.annotation_id = revision.annotation_id
    WHERE revision.revision_id = NEW.revision_id;
    IF revision_case IS DISTINCT FROM adjudication_case
       OR revision_visibility IS DISTINCT FROM adjudication_visibility
       OR revision_organization IS DISTINCT FROM adjudication_organization THEN
        RAISE EXCEPTION 'considered annotation revision scope must match adjudication';
    END IF;
    RETURN NEW;
END;
$$;

CREATE TRIGGER adjudication_considered_revision_scope
    BEFORE INSERT OR UPDATE ON adjudication_considered_revisions
    FOR EACH ROW EXECUTE FUNCTION hiveblot_enforce_adjudication_considered_scope();

CREATE FUNCTION hiveblot_enforce_job_scope() RETURNS trigger
LANGUAGE plpgsql AS $$
DECLARE
    input_item JSONB;
    parent_job UUID;
    parent_visibility TEXT;
    parent_organization UUID;
    artifact_visibility TEXT;
    artifact_organization UUID;
BEGIN
    IF COALESCE(NEW.specification->>'visibility', 'public') <> NEW.visibility
       OR NULLIF(NEW.specification->>'organization_id', '')::uuid
            IS DISTINCT FROM NEW.organization_id THEN
        RAISE EXCEPTION 'job columns must match specification scope';
    END IF;
    IF NULLIF(NEW.specification->>'submitted_by', '')::uuid
         IS DISTINCT FROM NEW.submitted_by THEN
        RAISE EXCEPTION 'job submitted_by column must match its specification';
    END IF;
    parent_job := NULLIF(NEW.specification->>'parent_job_id', '')::uuid;
    IF parent_job IS NOT NULL THEN
        SELECT visibility, organization_id INTO parent_visibility, parent_organization
        FROM jobs WHERE job_id = parent_job;
        IF parent_visibility IS NULL THEN
            RAISE EXCEPTION 'job parent does not exist';
        END IF;
        IF NEW.visibility = 'public' AND parent_visibility <> 'public' THEN
            RAISE EXCEPTION 'public jobs cannot reference private parent jobs';
        END IF;
        IF parent_visibility = 'organization_private'
           AND parent_organization IS DISTINCT FROM NEW.organization_id THEN
            RAISE EXCEPTION 'private parent jobs must belong to the job organization';
        END IF;
    END IF;
    FOR input_item IN SELECT value FROM jsonb_array_elements(
        COALESCE(NEW.specification->'inputs', '[]'::jsonb)
    ) LOOP
        SELECT visibility, organization_id INTO artifact_visibility, artifact_organization
        FROM artifacts
        WHERE artifact_id = (input_item->'artifact'->>'artifact_id')::uuid;
        IF NEW.visibility = 'public' AND artifact_visibility <> 'public' THEN
            RAISE EXCEPTION 'public jobs may reference only public artifacts';
        END IF;
        IF artifact_visibility = 'organization_private'
           AND artifact_organization IS DISTINCT FROM NEW.organization_id THEN
            RAISE EXCEPTION 'private job inputs must belong to the job organization';
        END IF;
    END LOOP;
    RETURN NEW;
END;
$$;

CREATE TRIGGER jobs_scope
    BEFORE INSERT OR UPDATE OF specification, visibility, organization_id, submitted_by ON jobs
    FOR EACH ROW EXECUTE FUNCTION hiveblot_enforce_job_scope();

CREATE FUNCTION hiveblot_enforce_job_output_scope() RETURNS trigger
LANGUAGE plpgsql AS $$
DECLARE
    job_visibility TEXT;
    job_organization UUID;
    artifact_visibility TEXT;
    artifact_organization UUID;
BEGIN
    SELECT visibility, organization_id INTO job_visibility, job_organization
    FROM jobs WHERE job_id = NEW.job_id;
    SELECT visibility, organization_id INTO artifact_visibility, artifact_organization
    FROM artifacts WHERE artifact_id = NEW.artifact_id;
    IF artifact_visibility IS DISTINCT FROM job_visibility
       OR artifact_organization IS DISTINCT FROM job_organization THEN
        RAISE EXCEPTION 'job output artifact scope must match the job';
    END IF;
    RETURN NEW;
END;
$$;

CREATE TRIGGER job_outputs_scope
    BEFORE INSERT OR UPDATE ON job_outputs
    FOR EACH ROW EXECUTE FUNCTION hiveblot_enforce_job_output_scope();

CREATE FUNCTION hiveblot_enforce_pipeline_run_scope() RETURNS trigger
LANGUAGE plpgsql AS $$
DECLARE
    case_visibility TEXT;
    case_organization UUID;
BEGIN
    SELECT visibility, organization_id INTO case_visibility, case_organization
    FROM evaluation_cases WHERE case_id = NEW.case_id;
    IF case_visibility = 'organization_private'
       AND (NEW.visibility <> 'organization_private'
            OR NEW.organization_id IS DISTINCT FROM case_organization) THEN
        RAISE EXCEPTION 'private cases require pipeline runs in the same organization';
    END IF;
    IF COALESCE(NEW.run_json->>'visibility', 'public') <> NEW.visibility
       OR NULLIF(NEW.run_json->>'organization_id', '')::uuid
            IS DISTINCT FROM NEW.organization_id THEN
        RAISE EXCEPTION 'pipeline run columns must match run_json scope';
    END IF;
    RETURN NEW;
END;
$$;

CREATE TRIGGER pipeline_run_scope
    BEFORE INSERT OR UPDATE OF case_id, run_json, visibility, organization_id ON pipeline_runs
    FOR EACH ROW EXECUTE FUNCTION hiveblot_enforce_pipeline_run_scope();

CREATE FUNCTION hiveblot_enforce_pipeline_publication_scope() RETURNS trigger
LANGUAGE plpgsql AS $$
DECLARE
    run_visibility TEXT;
    run_organization UUID;
BEGIN
    SELECT visibility, organization_id INTO run_visibility, run_organization
    FROM pipeline_runs WHERE run_id = NEW.run_id;
    IF NEW.visibility IS DISTINCT FROM run_visibility
       OR NEW.organization_id IS DISTINCT FROM run_organization THEN
        RAISE EXCEPTION 'pipeline publication scope must match its run';
    END IF;
    IF COALESCE(NEW.publication_json->>'visibility', 'public') <> NEW.visibility
       OR NULLIF(NEW.publication_json->>'organization_id', '')::uuid
            IS DISTINCT FROM NEW.organization_id THEN
        RAISE EXCEPTION 'pipeline publication columns must match publication_json scope';
    END IF;
    RETURN NEW;
END;
$$;

CREATE TRIGGER pipeline_publication_scope
    BEFORE INSERT OR UPDATE OF run_id, publication_json, visibility, organization_id
    ON pipeline_publications
    FOR EACH ROW EXECUTE FUNCTION hiveblot_enforce_pipeline_publication_scope();

CREATE FUNCTION hiveblot_artifact_matches_scope(
    target_visibility TEXT,
    target_organization UUID,
    target_artifact_id UUID,
    exact_scope BOOLEAN
) RETURNS BOOLEAN
LANGUAGE sql STABLE AS $$
    SELECT CASE
        WHEN exact_scope THEN
            artifact.visibility = target_visibility
            AND artifact.organization_id IS NOT DISTINCT FROM target_organization
        ELSE
            artifact.visibility = 'public'
            OR (
                artifact.visibility = 'organization_private'
                AND artifact.organization_id IS NOT DISTINCT FROM target_organization
            )
        END
    FROM artifacts AS artifact
    WHERE artifact.artifact_id = target_artifact_id
$$;

CREATE FUNCTION hiveblot_enforce_pipeline_run_artifact_scope() RETURNS trigger
LANGUAGE plpgsql AS $$
DECLARE
    run_visibility TEXT;
    run_organization UUID;
BEGIN
    SELECT visibility, organization_id INTO run_visibility, run_organization
    FROM pipeline_runs WHERE run_id = NEW.run_id;
    IF NOT COALESCE(
        hiveblot_artifact_matches_scope(
            run_visibility,
            run_organization,
            NEW.artifact_id,
            FALSE
        ),
        FALSE
    ) THEN
        RAISE EXCEPTION 'pipeline input artifact is outside the pipeline run scope';
    END IF;
    RETURN NEW;
END;
$$;

CREATE TRIGGER pipeline_run_inputs_scope
    BEFORE INSERT OR UPDATE ON pipeline_run_input_artifacts
    FOR EACH ROW EXECUTE FUNCTION hiveblot_enforce_pipeline_run_artifact_scope();

CREATE FUNCTION hiveblot_enforce_component_artifact_scope() RETURNS trigger
LANGUAGE plpgsql AS $$
DECLARE
    run_visibility TEXT;
    run_organization UUID;
    require_exact_scope BOOLEAN;
BEGIN
    SELECT run.visibility, run.organization_id INTO run_visibility, run_organization
    FROM component_invocations AS invocation
    JOIN pipeline_runs AS run ON run.run_id = invocation.run_id
    WHERE invocation.invocation_id = NEW.invocation_id;
    require_exact_scope := TG_TABLE_NAME = 'component_invocation_output_artifacts';
    IF NOT COALESCE(
        hiveblot_artifact_matches_scope(
            run_visibility,
            run_organization,
            NEW.artifact_id,
            require_exact_scope
        ),
        FALSE
    ) THEN
        RAISE EXCEPTION 'component artifact is outside the pipeline run scope';
    END IF;
    RETURN NEW;
END;
$$;

CREATE TRIGGER component_inputs_scope
    BEFORE INSERT OR UPDATE ON component_invocation_input_artifacts
    FOR EACH ROW EXECUTE FUNCTION hiveblot_enforce_component_artifact_scope();
CREATE TRIGGER component_outputs_scope
    BEFORE INSERT OR UPDATE ON component_invocation_output_artifacts
    FOR EACH ROW EXECUTE FUNCTION hiveblot_enforce_component_artifact_scope();
CREATE TRIGGER component_evidence_scope
    BEFORE INSERT OR UPDATE ON component_invocation_evidence_artifacts
    FOR EACH ROW EXECUTE FUNCTION hiveblot_enforce_component_artifact_scope();

CREATE FUNCTION hiveblot_enforce_pipeline_publication_artifact_scope() RETURNS trigger
LANGUAGE plpgsql AS $$
DECLARE
    publication_visibility TEXT;
    publication_organization UUID;
BEGIN
    SELECT visibility, organization_id
        INTO publication_visibility, publication_organization
    FROM pipeline_publications WHERE publication_id = NEW.publication_id;
    IF NOT COALESCE(
        hiveblot_artifact_matches_scope(
            publication_visibility,
            publication_organization,
            NEW.artifact_id,
            TRUE
        ),
        FALSE
    ) THEN
        RAISE EXCEPTION 'pipeline publication artifact scope must match the publication';
    END IF;
    RETURN NEW;
END;
$$;

CREATE TRIGGER pipeline_publication_outputs_scope
    BEFORE INSERT OR UPDATE ON pipeline_publication_output_artifacts
    FOR EACH ROW EXECUTE FUNCTION hiveblot_enforce_pipeline_publication_artifact_scope();
