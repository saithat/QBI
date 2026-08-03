INSERT INTO auth_users (
    user_id, email, display_name, user_status, created_at, updated_at
) VALUES (
    '00000000-0000-0000-0000-000000000001',
    'system@hiveblot.invalid',
    'HiveBlot platform operator',
    'active',
    transaction_timestamp(),
    transaction_timestamp()
) ON CONFLICT (user_id) DO NOTHING;

CREATE FUNCTION hiveblot_protect_platform_operator_membership() RETURNS trigger
LANGUAGE plpgsql AS $$
BEGIN
    IF NEW.user_id = '00000000-0000-0000-0000-000000000001' THEN
        RAISE EXCEPTION 'the reserved platform identity cannot join an organization';
    END IF;
    RETURN NEW;
END;
$$;

CREATE TRIGGER organization_membership_platform_operator_guard
    BEFORE INSERT OR UPDATE OF user_id ON organization_memberships
    FOR EACH ROW EXECUTE FUNCTION hiveblot_protect_platform_operator_membership();

CREATE TABLE evidence_index_configurations (
    configuration_id UUID PRIMARY KEY,
    index_name TEXT NOT NULL CHECK (char_length(btrim(index_name)) BETWEEN 1 AND 200),
    configuration_version TEXT NOT NULL CHECK (
        char_length(btrim(configuration_version)) BETWEEN 1 AND 200
    ),
    embedding_provider TEXT NOT NULL CHECK (
        char_length(btrim(embedding_provider)) BETWEEN 1 AND 200
    ),
    embedding_name TEXT NOT NULL CHECK (char_length(btrim(embedding_name)) BETWEEN 1 AND 200),
    embedding_version TEXT NOT NULL CHECK (
        char_length(btrim(embedding_version)) BETWEEN 1 AND 200
    ),
    embedding_dimensions INTEGER NOT NULL CHECK (embedding_dimensions BETWEEN 8 AND 4096),
    configuration_sha256 CHAR(64) NOT NULL UNIQUE CHECK (
        configuration_sha256 ~ '^[a-f0-9]{64}$'
    ),
    configuration_json JSONB NOT NULL CHECK (jsonb_typeof(configuration_json) = 'object'),
    created_by UUID NOT NULL REFERENCES auth_users(user_id) ON DELETE RESTRICT,
    created_at TIMESTAMPTZ NOT NULL,
    UNIQUE (index_name, configuration_version)
);

CREATE TABLE evidence_index_versions (
    index_version_id UUID PRIMARY KEY,
    configuration_id UUID NOT NULL REFERENCES evidence_index_configurations(configuration_id),
    index_name TEXT NOT NULL CHECK (char_length(btrim(index_name)) BETWEEN 1 AND 200),
    index_version TEXT NOT NULL CHECK (char_length(btrim(index_version)) BETWEEN 1 AND 200),
    index_status TEXT NOT NULL CHECK (
        index_status IN ('building', 'ready', 'active', 'failed', 'retired')
    ),
    document_count INTEGER NOT NULL DEFAULT 0 CHECK (document_count >= 0),
    manifest_sha256 CHAR(64) CHECK (manifest_sha256 ~ '^[a-f0-9]{64}$'),
    failure_reason TEXT CHECK (
        failure_reason IS NULL OR char_length(failure_reason) BETWEEN 1 AND 4000
    ),
    created_by UUID NOT NULL REFERENCES auth_users(user_id) ON DELETE RESTRICT,
    created_at TIMESTAMPTZ NOT NULL,
    built_at TIMESTAMPTZ,
    evaluated_at TIMESTAMPTZ,
    activated_at TIMESTAMPTZ,
    activation_evaluation_run_id UUID,
    UNIQUE (index_name, index_version),
    CHECK (
        (index_status = 'building' AND built_at IS NULL AND manifest_sha256 IS NULL)
        OR (index_status = 'failed' AND failure_reason IS NOT NULL)
        OR (index_status IN ('ready', 'active', 'retired')
            AND built_at IS NOT NULL AND manifest_sha256 IS NOT NULL)
    ),
    CHECK (
        (index_status IN ('active', 'retired'))
        = (activated_at IS NOT NULL AND activation_evaluation_run_id IS NOT NULL)
    ),
    CHECK (activated_at IS NULL OR evaluated_at IS NOT NULL),
    CHECK (built_at IS NULL OR built_at >= created_at),
    CHECK (evaluated_at IS NULL OR evaluated_at >= created_at),
    CHECK (activated_at IS NULL OR activated_at >= created_at)
);

CREATE UNIQUE INDEX evidence_index_versions_active_idx
    ON evidence_index_versions (index_name)
    WHERE index_status = 'active';
CREATE INDEX evidence_index_versions_history_idx
    ON evidence_index_versions (index_name, created_at DESC, index_version_id DESC);

CREATE TABLE evidence_index_documents (
    index_version_id UUID NOT NULL REFERENCES evidence_index_versions(index_version_id),
    document_id UUID NOT NULL,
    case_id UUID NOT NULL REFERENCES evaluation_cases(case_id),
    source_annotation_revision_id UUID REFERENCES annotation_revisions(revision_id),
    source_pipeline_publication_id UUID REFERENCES pipeline_publications(publication_id),
    paper_id TEXT NOT NULL CHECK (char_length(btrim(paper_id)) BETWEEN 1 AND 200),
    figure_label TEXT CHECK (figure_label IS NULL OR char_length(figure_label) BETWEEN 1 AND 1000),
    experiment_label TEXT CHECK (
        experiment_label IS NULL OR char_length(experiment_label) BETWEEN 1 AND 2000
    ),
    protein_terms TEXT[] NOT NULL DEFAULT '{}',
    biological_system_terms TEXT[] NOT NULL DEFAULT '{}',
    treatment_terms TEXT[] NOT NULL DEFAULT '{}',
    condition_terms TEXT[] NOT NULL DEFAULT '{}',
    review_status TEXT NOT NULL CHECK (
        review_status IN ('unreviewed', 'in_review', 'reviewed', 'needs_adjudication', 'adjudicated')
    ),
    evidence_quality TEXT NOT NULL CHECK (
        evidence_quality IN (
            'raw_source', 'supplementary_source', 'publication_figure',
            'exploratory', 'not_assessed'
        )
    ),
    visibility TEXT NOT NULL CHECK (visibility IN ('public', 'organization_private')),
    organization_id UUID REFERENCES organizations(organization_id) ON DELETE RESTRICT,
    search_text TEXT NOT NULL CHECK (char_length(btrim(search_text)) BETWEEN 1 AND 500000),
    search_vector TSVECTOR GENERATED ALWAYS AS (
        to_tsvector('english', search_text)
    ) STORED,
    embedding DOUBLE PRECISION[] NOT NULL CHECK (array_length(embedding, 1) BETWEEN 8 AND 4096),
    document_sha256 CHAR(64) NOT NULL CHECK (document_sha256 ~ '^[a-f0-9]{64}$'),
    document_json JSONB NOT NULL CHECK (jsonb_typeof(document_json) = 'object'),
    created_at TIMESTAMPTZ NOT NULL,
    PRIMARY KEY (index_version_id, document_id),
    UNIQUE (index_version_id, document_sha256),
    CHECK (
        source_annotation_revision_id IS NOT NULL
        OR source_pipeline_publication_id IS NOT NULL
    ),
    CHECK (
        (visibility = 'public' AND organization_id IS NULL)
        OR (visibility = 'organization_private' AND organization_id IS NOT NULL)
    )
);

CREATE INDEX evidence_index_documents_scope_idx
    ON evidence_index_documents (index_version_id, visibility, organization_id, document_id);
CREATE INDEX evidence_index_documents_search_idx
    ON evidence_index_documents USING GIN (search_vector);
CREATE INDEX evidence_index_documents_proteins_idx
    ON evidence_index_documents USING GIN (protein_terms);
CREATE INDEX evidence_index_documents_biological_systems_idx
    ON evidence_index_documents USING GIN (biological_system_terms);
CREATE INDEX evidence_index_documents_treatments_idx
    ON evidence_index_documents USING GIN (treatment_terms);
CREATE INDEX evidence_index_documents_conditions_idx
    ON evidence_index_documents USING GIN (condition_terms);

CREATE TABLE evidence_index_document_citations (
    index_version_id UUID NOT NULL,
    document_id UUID NOT NULL,
    artifact_id UUID NOT NULL REFERENCES artifacts(artifact_id),
    citation_position INTEGER NOT NULL CHECK (citation_position >= 0),
    PRIMARY KEY (index_version_id, document_id, artifact_id),
    UNIQUE (index_version_id, document_id, citation_position),
    FOREIGN KEY (index_version_id, document_id)
        REFERENCES evidence_index_documents(index_version_id, document_id)
        ON DELETE CASCADE
);

CREATE INDEX evidence_index_document_citations_artifact_idx
    ON evidence_index_document_citations (artifact_id, index_version_id, document_id);

CREATE TABLE retrieval_evaluation_datasets (
    dataset_id UUID PRIMARY KEY,
    dataset_name TEXT NOT NULL CHECK (char_length(btrim(dataset_name)) BETWEEN 1 AND 200),
    dataset_version TEXT NOT NULL CHECK (char_length(btrim(dataset_version)) BETWEEN 1 AND 200),
    visibility TEXT NOT NULL CHECK (visibility IN ('public', 'organization_private')),
    organization_id UUID REFERENCES organizations(organization_id) ON DELETE RESTRICT,
    content_sha256 CHAR(64) NOT NULL CHECK (content_sha256 ~ '^[a-f0-9]{64}$'),
    dataset_json JSONB NOT NULL CHECK (jsonb_typeof(dataset_json) = 'object'),
    frozen_by UUID NOT NULL REFERENCES auth_users(user_id) ON DELETE RESTRICT,
    frozen_at TIMESTAMPTZ NOT NULL,
    UNIQUE (dataset_name, dataset_version),
    UNIQUE (content_sha256),
    CHECK (
        (visibility = 'public' AND organization_id IS NULL)
        OR (visibility = 'organization_private' AND organization_id IS NOT NULL)
    )
);

CREATE TABLE retrieval_evaluation_runs (
    evaluation_run_id UUID PRIMARY KEY,
    dataset_id UUID NOT NULL REFERENCES retrieval_evaluation_datasets(dataset_id),
    dataset_sha256 CHAR(64) NOT NULL CHECK (dataset_sha256 ~ '^[a-f0-9]{64}$'),
    index_version_id UUID NOT NULL REFERENCES evidence_index_versions(index_version_id),
    scorer_name TEXT NOT NULL CHECK (char_length(btrim(scorer_name)) BETWEEN 1 AND 200),
    scorer_version TEXT NOT NULL CHECK (char_length(btrim(scorer_version)) BETWEEN 1 AND 200),
    passed BOOLEAN NOT NULL,
    run_json JSONB NOT NULL CHECK (jsonb_typeof(run_json) = 'object'),
    created_at TIMESTAMPTZ NOT NULL
);

CREATE INDEX retrieval_evaluation_runs_version_idx
    ON retrieval_evaluation_runs (index_version_id, passed, created_at DESC);

ALTER TABLE evidence_index_versions
    ADD CONSTRAINT evidence_index_versions_activation_evaluation_fk
    FOREIGN KEY (activation_evaluation_run_id)
    REFERENCES retrieval_evaluation_runs(evaluation_run_id)
    ON DELETE RESTRICT;

CREATE FUNCTION hiveblot_enforce_index_configuration_immutable() RETURNS trigger
LANGUAGE plpgsql AS $$
BEGIN
    RAISE EXCEPTION 'search index configurations are immutable';
END;
$$;

CREATE FUNCTION hiveblot_enforce_index_configuration_json() RETURNS trigger
LANGUAGE plpgsql AS $$
BEGIN
    IF NEW.configuration_json->>'configuration_id' IS DISTINCT FROM NEW.configuration_id::text
       OR NEW.configuration_json->>'index_name' IS DISTINCT FROM NEW.index_name
       OR NEW.configuration_json->>'configuration_version'
          IS DISTINCT FROM NEW.configuration_version
       OR NEW.configuration_json->'embedding_model'->>'provider'
          IS DISTINCT FROM NEW.embedding_provider
       OR NEW.configuration_json->'embedding_model'->>'name'
          IS DISTINCT FROM NEW.embedding_name
       OR NEW.configuration_json->'embedding_model'->>'version'
          IS DISTINCT FROM NEW.embedding_version
       OR (NEW.configuration_json->>'embedding_dimensions')::integer
          IS DISTINCT FROM NEW.embedding_dimensions
       OR NEW.configuration_json->>'configuration_sha256'
          IS DISTINCT FROM NEW.configuration_sha256
       OR NEW.configuration_json->>'created_by' IS DISTINCT FROM NEW.created_by::text THEN
        RAISE EXCEPTION 'index configuration JSON must match indexed columns';
    END IF;
    RETURN NEW;
END;
$$;

CREATE TRIGGER evidence_index_configuration_json
    BEFORE INSERT OR UPDATE ON evidence_index_configurations
    FOR EACH ROW EXECUTE FUNCTION hiveblot_enforce_index_configuration_json();

CREATE TRIGGER evidence_index_configurations_immutable
    BEFORE UPDATE OR DELETE ON evidence_index_configurations
    FOR EACH ROW EXECUTE FUNCTION hiveblot_enforce_index_configuration_immutable();

CREATE FUNCTION hiveblot_enforce_index_version_configuration() RETURNS trigger
LANGUAGE plpgsql AS $$
DECLARE
    configured_name TEXT;
BEGIN
    SELECT index_name INTO configured_name
    FROM evidence_index_configurations
    WHERE configuration_id = NEW.configuration_id;
    IF configured_name IS DISTINCT FROM NEW.index_name THEN
        RAISE EXCEPTION 'index version name must match its configuration';
    END IF;
    RETURN NEW;
END;
$$;

CREATE TRIGGER evidence_index_version_configuration
    BEFORE INSERT OR UPDATE OF configuration_id, index_name ON evidence_index_versions
    FOR EACH ROW EXECUTE FUNCTION hiveblot_enforce_index_version_configuration();

CREATE FUNCTION hiveblot_enforce_index_version_lifecycle() RETURNS trigger
LANGUAGE plpgsql AS $$
BEGIN
    IF OLD.index_version_id IS DISTINCT FROM NEW.index_version_id
       OR OLD.configuration_id IS DISTINCT FROM NEW.configuration_id
       OR OLD.index_name IS DISTINCT FROM NEW.index_name
       OR OLD.index_version IS DISTINCT FROM NEW.index_version
       OR OLD.created_by IS DISTINCT FROM NEW.created_by
       OR OLD.created_at IS DISTINCT FROM NEW.created_at THEN
        RAISE EXCEPTION 'stable index version identity cannot change';
    END IF;
    IF OLD.activation_evaluation_run_id IS NOT NULL
       AND (
           OLD.activation_evaluation_run_id
              IS DISTINCT FROM NEW.activation_evaluation_run_id
           OR OLD.activated_at IS DISTINCT FROM NEW.activated_at
       ) THEN
        RAISE EXCEPTION 'index activation provenance cannot change';
    END IF;
    IF OLD.index_status = NEW.index_status THEN
        IF OLD.index_status NOT IN ('ready', 'active')
           OR NEW.evaluated_at IS NULL
           OR OLD.evaluated_at IS NOT NULL THEN
            RAISE EXCEPTION 'invalid same-state index version update';
        END IF;
    ELSIF NOT (
        (OLD.index_status = 'building' AND NEW.index_status IN ('ready', 'failed'))
        OR (OLD.index_status = 'ready' AND NEW.index_status = 'active')
        OR (OLD.index_status = 'active' AND NEW.index_status = 'retired')
    ) THEN
        RAISE EXCEPTION 'invalid index version lifecycle transition';
    END IF;
    IF NEW.index_status = 'active' AND NOT EXISTS (
        SELECT 1 FROM retrieval_evaluation_runs AS evaluation
        WHERE evaluation.evaluation_run_id = NEW.activation_evaluation_run_id
          AND evaluation.index_version_id = NEW.index_version_id
          AND evaluation.passed = TRUE
          AND evaluation.created_at <= NEW.activated_at
    ) THEN
        RAISE EXCEPTION 'index activation requires its selected passing retrieval evaluation';
    END IF;
    IF NEW.index_status = 'ready' THEN
        IF NEW.document_count IS DISTINCT FROM (
            SELECT COUNT(*)::integer FROM evidence_index_documents
            WHERE index_version_id = NEW.index_version_id
        ) THEN
            RAISE EXCEPTION 'index document count does not match published rows';
        END IF;
        IF EXISTS (
            SELECT 1
            FROM evidence_index_documents AS document
            WHERE document.index_version_id = NEW.index_version_id
              AND (
                  jsonb_array_length(document.document_json->'citations')
                  <> (
                      SELECT COUNT(*) FROM evidence_index_document_citations AS citation
                      WHERE citation.index_version_id = document.index_version_id
                        AND citation.document_id = document.document_id
                  )
                  OR EXISTS (
                      SELECT 1
                      FROM jsonb_array_elements(document.document_json->'citations') AS expected
                      WHERE NOT EXISTS (
                          SELECT 1 FROM evidence_index_document_citations AS citation
                          WHERE citation.index_version_id = document.index_version_id
                            AND citation.document_id = document.document_id
                            AND citation.artifact_id =
                                (expected->'artifact'->>'artifact_id')::uuid
                      )
                  )
              )
        ) THEN
            RAISE EXCEPTION 'index citation rows do not match document JSON';
        END IF;
    END IF;
    RETURN NEW;
END;
$$;

CREATE TRIGGER evidence_index_version_lifecycle
    BEFORE UPDATE ON evidence_index_versions
    FOR EACH ROW EXECUTE FUNCTION hiveblot_enforce_index_version_lifecycle();

CREATE FUNCTION hiveblot_enforce_index_document() RETURNS trigger
LANGUAGE plpgsql AS $$
DECLARE
    version_status TEXT;
    case_visibility TEXT;
    case_organization UUID;
    source_visibility TEXT;
    source_organization UUID;
    source_case UUID;
    configured_dimensions INTEGER;
    expected_terms TEXT[];
BEGIN
    SELECT index_status INTO version_status
    FROM evidence_index_versions WHERE index_version_id = NEW.index_version_id;
    IF version_status <> 'building' THEN
        RAISE EXCEPTION 'index documents may be written only while the version is building';
    END IF;
    SELECT configuration.embedding_dimensions INTO configured_dimensions
    FROM evidence_index_versions AS version
    JOIN evidence_index_configurations AS configuration
      ON configuration.configuration_id = version.configuration_id
    WHERE version.index_version_id = NEW.index_version_id;
    IF array_length(NEW.embedding, 1) IS DISTINCT FROM configured_dimensions THEN
        RAISE EXCEPTION 'document embedding dimensions must match index configuration';
    END IF;
    SELECT visibility, organization_id INTO case_visibility, case_organization
    FROM evaluation_cases WHERE case_id = NEW.case_id;
    IF case_visibility = 'organization_private'
       AND (NEW.visibility <> 'organization_private'
            OR NEW.organization_id IS DISTINCT FROM case_organization) THEN
        RAISE EXCEPTION 'private cases require same-organization index documents';
    END IF;
    IF NEW.source_annotation_revision_id IS NOT NULL THEN
        SELECT annotation.visibility, annotation.organization_id, annotation.case_id
        INTO source_visibility, source_organization, source_case
        FROM annotation_revisions AS revision
        JOIN annotation_documents AS annotation
          ON annotation.annotation_id = revision.annotation_id
        WHERE revision.revision_id = NEW.source_annotation_revision_id;
        IF NEW.visibility = 'public' AND source_visibility <> 'public' THEN
            RAISE EXCEPTION 'public index documents cannot use private annotations';
        END IF;
        IF source_visibility = 'organization_private'
           AND source_organization IS DISTINCT FROM NEW.organization_id THEN
            RAISE EXCEPTION 'private annotation source must match index document organization';
        END IF;
        IF source_case IS DISTINCT FROM NEW.case_id THEN
            RAISE EXCEPTION 'annotation source must belong to the indexed case';
        END IF;
    END IF;
    IF NEW.source_pipeline_publication_id IS NOT NULL THEN
        SELECT visibility, organization_id, case_id
        INTO source_visibility, source_organization, source_case
        FROM pipeline_publications
        WHERE publication_id = NEW.source_pipeline_publication_id;
        IF NEW.visibility = 'public' AND source_visibility <> 'public' THEN
            RAISE EXCEPTION 'public index documents cannot use private publications';
        END IF;
        IF source_visibility = 'organization_private'
           AND source_organization IS DISTINCT FROM NEW.organization_id THEN
            RAISE EXCEPTION 'private publication source must match index document organization';
        END IF;
        IF source_case IS DISTINCT FROM NEW.case_id THEN
            RAISE EXCEPTION 'pipeline publication source must belong to the indexed case';
        END IF;
    END IF;
    IF NEW.document_json->>'document_id' IS DISTINCT FROM NEW.document_id::text
       OR NEW.document_json->>'case_id' IS DISTINCT FROM NEW.case_id::text
       OR NEW.document_json->>'visibility' IS DISTINCT FROM NEW.visibility
       OR NULLIF(NEW.document_json->>'organization_id', '')::uuid
          IS DISTINCT FROM NEW.organization_id
       OR NULLIF(NEW.document_json->>'source_annotation_revision_id', '')::uuid
          IS DISTINCT FROM NEW.source_annotation_revision_id
       OR NULLIF(NEW.document_json->>'source_pipeline_publication_id', '')::uuid
          IS DISTINCT FROM NEW.source_pipeline_publication_id
       OR NEW.document_json->>'paper_id' IS DISTINCT FROM NEW.paper_id
       OR NEW.document_json->>'review_status' IS DISTINCT FROM NEW.review_status
       OR NEW.document_json->>'evidence_quality' IS DISTINCT FROM NEW.evidence_quality THEN
        RAISE EXCEPTION 'index document JSON must match indexed identity and scope';
    END IF;
    FOR expected_terms, source_visibility IN
        SELECT NEW.protein_terms, 'proteins'
        UNION ALL SELECT NEW.biological_system_terms, 'biological_systems'
        UNION ALL SELECT NEW.treatment_terms, 'treatments'
        UNION ALL SELECT NEW.condition_terms, 'conditions'
    LOOP
        IF expected_terms IS DISTINCT FROM ARRAY(
            SELECT lower(btrim(value))
            FROM jsonb_array_elements_text(NEW.document_json->source_visibility) AS value
            ORDER BY lower(btrim(value))
        ) THEN
            RAISE EXCEPTION 'structured index terms must match document JSON';
        END IF;
    END LOOP;
    RETURN NEW;
END;
$$;

CREATE TRIGGER evidence_index_document_integrity
    BEFORE INSERT OR UPDATE ON evidence_index_documents
    FOR EACH ROW EXECUTE FUNCTION hiveblot_enforce_index_document();

CREATE FUNCTION hiveblot_prevent_completed_index_document_mutation() RETURNS trigger
LANGUAGE plpgsql AS $$
DECLARE
    version_status TEXT;
BEGIN
    SELECT index_status INTO version_status
    FROM evidence_index_versions WHERE index_version_id = OLD.index_version_id;
    IF version_status <> 'building' THEN
        RAISE EXCEPTION 'completed index documents are immutable';
    END IF;
    RETURN OLD;
END;
$$;

CREATE TRIGGER evidence_index_document_immutable
    BEFORE DELETE ON evidence_index_documents
    FOR EACH ROW EXECUTE FUNCTION hiveblot_prevent_completed_index_document_mutation();

CREATE TRIGGER evidence_index_citation_immutable
    BEFORE DELETE ON evidence_index_document_citations
    FOR EACH ROW EXECUTE FUNCTION hiveblot_prevent_completed_index_document_mutation();

CREATE FUNCTION hiveblot_enforce_index_citation_scope() RETURNS trigger
LANGUAGE plpgsql AS $$
DECLARE
    document_visibility TEXT;
    document_organization UUID;
    artifact_visibility TEXT;
    artifact_organization UUID;
    artifact_sha256 CHAR(64);
    artifact_media_type TEXT;
    artifact_byte_size BIGINT;
    citation_json JSONB;
    version_status TEXT;
BEGIN
    SELECT document.visibility, document.organization_id, version.index_status,
           document.document_json->'citations'->NEW.citation_position
    INTO document_visibility, document_organization, version_status, citation_json
    FROM evidence_index_documents AS document
    JOIN evidence_index_versions AS version
      ON version.index_version_id = document.index_version_id
    WHERE document.index_version_id = NEW.index_version_id
      AND document.document_id = NEW.document_id;
    IF version_status <> 'building' THEN
        RAISE EXCEPTION 'index citations may be written only while the version is building';
    END IF;
    SELECT artifact.visibility, artifact.organization_id, artifact.blob_sha256,
           blob.media_type, blob.byte_size
    INTO artifact_visibility, artifact_organization, artifact_sha256,
         artifact_media_type, artifact_byte_size
    FROM artifacts AS artifact
    JOIN artifact_blobs AS blob ON blob.sha256 = artifact.blob_sha256
    WHERE artifact.artifact_id = NEW.artifact_id;
    IF document_visibility = 'public' AND artifact_visibility <> 'public' THEN
        RAISE EXCEPTION 'public index documents cannot cite private artifacts';
    END IF;
    IF artifact_visibility = 'organization_private'
       AND artifact_organization IS DISTINCT FROM document_organization THEN
        RAISE EXCEPTION 'private citation artifact must match index document organization';
    END IF;
    IF citation_json IS NULL
       OR citation_json->'artifact'->>'artifact_id' IS DISTINCT FROM NEW.artifact_id::text
       OR citation_json->'artifact'->>'sha256' IS DISTINCT FROM artifact_sha256
       OR citation_json->'artifact'->>'media_type' IS DISTINCT FROM artifact_media_type
       OR (citation_json->'artifact'->>'byte_size')::bigint
          IS DISTINCT FROM artifact_byte_size THEN
        RAISE EXCEPTION 'citation metadata must match the immutable artifact';
    END IF;
    RETURN NEW;
END;
$$;

CREATE TRIGGER evidence_index_document_citation_scope
    BEFORE INSERT OR UPDATE ON evidence_index_document_citations
    FOR EACH ROW EXECUTE FUNCTION hiveblot_enforce_index_citation_scope();

CREATE FUNCTION hiveblot_prevent_retrieval_snapshot_mutation() RETURNS trigger
LANGUAGE plpgsql AS $$
BEGIN
    RAISE EXCEPTION 'retrieval evaluation snapshots are append-only';
END;
$$;

CREATE TRIGGER retrieval_evaluation_datasets_immutable
    BEFORE UPDATE OR DELETE ON retrieval_evaluation_datasets
    FOR EACH ROW EXECUTE FUNCTION hiveblot_prevent_retrieval_snapshot_mutation();
CREATE TRIGGER retrieval_evaluation_runs_immutable
    BEFORE UPDATE OR DELETE ON retrieval_evaluation_runs
    FOR EACH ROW EXECUTE FUNCTION hiveblot_prevent_retrieval_snapshot_mutation();

CREATE FUNCTION hiveblot_enforce_retrieval_dataset_json() RETURNS trigger
LANGUAGE plpgsql AS $$
BEGIN
    IF NEW.dataset_json->>'dataset_id' IS DISTINCT FROM NEW.dataset_id::text
       OR NEW.dataset_json->>'dataset_name' IS DISTINCT FROM NEW.dataset_name
       OR NEW.dataset_json->>'dataset_version' IS DISTINCT FROM NEW.dataset_version
       OR NEW.dataset_json->>'visibility' IS DISTINCT FROM NEW.visibility
       OR NULLIF(NEW.dataset_json->>'organization_id', '')::uuid
          IS DISTINCT FROM NEW.organization_id
       OR NEW.dataset_json->>'content_sha256' IS DISTINCT FROM NEW.content_sha256
       OR NEW.dataset_json->>'frozen_by' IS DISTINCT FROM NEW.frozen_by::text THEN
        RAISE EXCEPTION 'retrieval dataset JSON must match indexed identity and scope';
    END IF;
    RETURN NEW;
END;
$$;

CREATE TRIGGER retrieval_evaluation_dataset_integrity
    BEFORE INSERT OR UPDATE ON retrieval_evaluation_datasets
    FOR EACH ROW EXECUTE FUNCTION hiveblot_enforce_retrieval_dataset_json();

CREATE FUNCTION hiveblot_enforce_retrieval_evaluation_run() RETURNS trigger
LANGUAGE plpgsql AS $$
DECLARE
    expected_sha CHAR(64);
    version_status TEXT;
BEGIN
    SELECT content_sha256 INTO expected_sha
    FROM retrieval_evaluation_datasets WHERE dataset_id = NEW.dataset_id;
    SELECT index_status INTO version_status
    FROM evidence_index_versions WHERE index_version_id = NEW.index_version_id;
    IF expected_sha IS DISTINCT FROM NEW.dataset_sha256 THEN
        RAISE EXCEPTION 'retrieval run dataset hash does not match frozen dataset';
    END IF;
    IF version_status NOT IN ('ready', 'active') THEN
        RAISE EXCEPTION 'retrieval evaluation requires a ready index version';
    END IF;
    IF NEW.run_json->>'evaluation_run_id' IS DISTINCT FROM NEW.evaluation_run_id::text
       OR NEW.run_json->>'dataset_id' IS DISTINCT FROM NEW.dataset_id::text
       OR NEW.run_json->>'index_version_id' IS DISTINCT FROM NEW.index_version_id::text
       OR (NEW.run_json->>'passed')::boolean IS DISTINCT FROM NEW.passed THEN
        RAISE EXCEPTION 'retrieval evaluation JSON must match indexed columns';
    END IF;
    RETURN NEW;
END;
$$;

CREATE TRIGGER retrieval_evaluation_run_integrity
    BEFORE INSERT ON retrieval_evaluation_runs
    FOR EACH ROW EXECUTE FUNCTION hiveblot_enforce_retrieval_evaluation_run();
