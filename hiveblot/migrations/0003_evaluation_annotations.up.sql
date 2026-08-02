CREATE TABLE evaluation_cases (
    case_id UUID PRIMARY KEY,
    case_key TEXT NOT NULL UNIQUE CHECK (length(btrim(case_key)) BETWEEN 1 AND 200),
    dataset_id UUID,
    assay_type TEXT NOT NULL DEFAULT 'western_blot' CHECK (assay_type = 'western_blot'),
    review_status TEXT NOT NULL DEFAULT 'unreviewed' CHECK (
        review_status IN (
            'unreviewed', 'in_review', 'reviewed', 'needs_adjudication', 'adjudicated'
        )
    ),
    version INTEGER NOT NULL DEFAULT 1 CHECK (version >= 1),
    created_at TIMESTAMPTZ NOT NULL,
    updated_at TIMESTAMPTZ NOT NULL
);

CREATE TABLE evaluation_case_artifacts (
    case_id UUID NOT NULL REFERENCES evaluation_cases (case_id),
    artifact_id UUID NOT NULL REFERENCES artifacts (artifact_id),
    artifact_role TEXT NOT NULL CHECK (
        artifact_role IN ('source_document', 'figure', 'raw_source', 'supplementary', 'context')
    ),
    page_number INTEGER CHECK (page_number IS NULL OR page_number >= 1),
    PRIMARY KEY (case_id, artifact_id, artifact_role)
);

CREATE TABLE prediction_documents (
    prediction_id UUID PRIMARY KEY,
    case_id UUID NOT NULL REFERENCES evaluation_cases (case_id),
    prediction_schema TEXT NOT NULL CHECK (length(btrim(prediction_schema)) BETWEEN 1 AND 200),
    prediction_schema_version TEXT NOT NULL CHECK (
        length(btrim(prediction_schema_version)) BETWEEN 1 AND 200
    ),
    producer_json JSONB NOT NULL,
    pipeline_json JSONB,
    raw_output_json TEXT NOT NULL CHECK (length(raw_output_json) >= 1),
    normalized_output_json TEXT CHECK (
        normalized_output_json IS NULL OR length(normalized_output_json) >= 1
    ),
    configuration_json TEXT NOT NULL CHECK (length(configuration_json) >= 2),
    evidence_json JSONB NOT NULL DEFAULT '[]'::jsonb,
    validation_issues_json JSONB NOT NULL DEFAULT '[]'::jsonb,
    confidence DOUBLE PRECISION CHECK (confidence IS NULL OR confidence BETWEEN 0 AND 1),
    trace_id UUID NOT NULL,
    latency_ms BIGINT NOT NULL CHECK (latency_ms >= 0),
    cost_microusd BIGINT NOT NULL CHECK (cost_microusd >= 0),
    created_at TIMESTAMPTZ NOT NULL
);

CREATE INDEX prediction_documents_case_time_idx
    ON prediction_documents (case_id, created_at, prediction_id);

CREATE TABLE annotation_error_codes (
    code TEXT PRIMARY KEY CHECK (length(btrim(code)) BETWEEN 1 AND 200),
    category TEXT NOT NULL CHECK (length(btrim(category)) BETWEEN 1 AND 200),
    description TEXT NOT NULL CHECK (length(btrim(description)) BETWEEN 1 AND 4000),
    active BOOLEAN NOT NULL DEFAULT TRUE,
    created_at TIMESTAMPTZ NOT NULL
);

INSERT INTO annotation_error_codes (code, category, description, created_at) VALUES
    ('missing_evidence', 'provenance', 'Prediction has no sufficient source evidence.', now()),
    ('incorrect_value', 'field', 'Predicted field value is incorrect.', now()),
    ('incorrect_geometry', 'spatial', 'Predicted source-image geometry is incorrect.', now()),
    ('ambiguous_source', 'source', 'Source material cannot support one unambiguous label.', now()),
    ('unsupported_layout', 'assay', 'Assay layout is not supported by the current schema.', now());

CREATE TABLE annotation_documents (
    annotation_id UUID PRIMARY KEY,
    case_id UUID NOT NULL REFERENCES evaluation_cases (case_id),
    reviewer_id UUID NOT NULL,
    head_revision_id UUID NOT NULL,
    revision_count INTEGER NOT NULL DEFAULT 1 CHECK (revision_count >= 1),
    created_at TIMESTAMPTZ NOT NULL,
    updated_at TIMESTAMPTZ NOT NULL,
    UNIQUE (case_id, reviewer_id)
);

CREATE TABLE annotation_revisions (
    revision_id UUID PRIMARY KEY,
    annotation_id UUID NOT NULL REFERENCES annotation_documents (annotation_id),
    revision_number INTEGER NOT NULL CHECK (revision_number >= 1),
    prior_revision_id UUID,
    reviewer_id UUID NOT NULL,
    rationale TEXT CHECK (rationale IS NULL OR length(rationale) <= 10000),
    created_at TIMESTAMPTZ NOT NULL,
    UNIQUE (annotation_id, revision_number),
    UNIQUE (annotation_id, revision_id),
    CONSTRAINT annotation_revision_prior_fk FOREIGN KEY (annotation_id, prior_revision_id)
        REFERENCES annotation_revisions (annotation_id, revision_id)
        DEFERRABLE INITIALLY DEFERRED,
    CONSTRAINT annotation_revision_initial_prior_check CHECK (
        (revision_number = 1 AND prior_revision_id IS NULL)
        OR (revision_number > 1 AND prior_revision_id IS NOT NULL)
    )
);

ALTER TABLE annotation_documents
    ADD CONSTRAINT annotation_document_head_fk
    FOREIGN KEY (annotation_id, head_revision_id)
    REFERENCES annotation_revisions (annotation_id, revision_id)
    DEFERRABLE INITIALLY DEFERRED;

CREATE TABLE spatial_annotations (
    revision_id UUID NOT NULL REFERENCES annotation_revisions (revision_id),
    spatial_annotation_id UUID NOT NULL,
    annotation_type TEXT NOT NULL CHECK (
        annotation_type IN (
            'figure', 'panel', 'blot', 'lane', 'protein_row', 'band', 'label',
            'quantification_plot'
        )
    ),
    observation_state TEXT NOT NULL CHECK (
        observation_state IN ('present', 'absent', 'unknown', 'ambiguous', 'not_applicable')
    ),
    source_artifact_id UUID NOT NULL REFERENCES artifacts (artifact_id),
    region_id UUID NOT NULL,
    x DOUBLE PRECISION NOT NULL CHECK (x >= 0),
    y DOUBLE PRECISION NOT NULL CHECK (y >= 0),
    width DOUBLE PRECISION NOT NULL CHECK (width > 0),
    height DOUBLE PRECISION NOT NULL CHECK (height > 0),
    canvas_width INTEGER NOT NULL CHECK (canvas_width > 0),
    canvas_height INTEGER NOT NULL CHECK (canvas_height > 0),
    page_number INTEGER CHECK (page_number IS NULL OR page_number >= 1),
    label TEXT CHECK (label IS NULL OR length(label) <= 1000),
    PRIMARY KEY (revision_id, spatial_annotation_id),
    UNIQUE (revision_id, region_id),
    CHECK (x + width <= canvas_width),
    CHECK (y + height <= canvas_height)
);

CREATE TABLE field_annotations (
    revision_id UUID NOT NULL REFERENCES annotation_revisions (revision_id),
    field_annotation_id UUID NOT NULL,
    target_kind TEXT NOT NULL CHECK (target_kind IN ('field_path', 'entity')),
    field_path TEXT,
    entity_id UUID,
    field_name TEXT,
    observation_state TEXT NOT NULL CHECK (
        observation_state IN ('present', 'absent', 'unknown', 'ambiguous', 'not_applicable')
    ),
    value_json JSONB,
    original_extracted_text TEXT CHECK (
        original_extracted_text IS NULL OR length(original_extracted_text) <= 10000
    ),
    notes TEXT CHECK (notes IS NULL OR length(notes) <= 10000),
    PRIMARY KEY (revision_id, field_annotation_id),
    CONSTRAINT field_annotation_target_check CHECK (
        (target_kind = 'field_path' AND field_path IS NOT NULL AND entity_id IS NULL
            AND field_name IS NULL)
        OR (target_kind = 'entity' AND field_path IS NULL AND entity_id IS NOT NULL)
    ),
    CONSTRAINT field_annotation_path_check CHECK (
        field_path IS NULL OR field_path ~ '^/(?:[^/~]|~[01])+(?:/(?:[^/~]|~[01])+)*$'
    ),
    CONSTRAINT field_annotation_value_check CHECK (
        value_json IS NULL OR jsonb_typeof(value_json) IN ('string', 'number', 'boolean')
    ),
    CONSTRAINT field_annotation_absent_value_check CHECK (
        observation_state NOT IN ('absent', 'not_applicable') OR value_json IS NULL
    )
);

CREATE TABLE field_annotation_evidence (
    revision_id UUID NOT NULL,
    field_annotation_id UUID NOT NULL,
    evidence_region_id UUID NOT NULL,
    PRIMARY KEY (revision_id, field_annotation_id, evidence_region_id),
    FOREIGN KEY (revision_id, field_annotation_id)
        REFERENCES field_annotations (revision_id, field_annotation_id)
);

CREATE TABLE annotation_relationships (
    revision_id UUID NOT NULL REFERENCES annotation_revisions (revision_id),
    relationship_id UUID NOT NULL,
    subject_id UUID NOT NULL,
    relation_type TEXT NOT NULL CHECK (length(btrim(relation_type)) BETWEEN 1 AND 200),
    object_id UUID NOT NULL,
    PRIMARY KEY (revision_id, relationship_id),
    CHECK (subject_id <> object_id)
);

CREATE TABLE annotation_revision_error_codes (
    revision_id UUID NOT NULL REFERENCES annotation_revisions (revision_id),
    error_code TEXT NOT NULL REFERENCES annotation_error_codes (code),
    PRIMARY KEY (revision_id, error_code)
);

CREATE TABLE reviewer_assignments (
    assignment_id UUID PRIMARY KEY,
    case_id UUID NOT NULL REFERENCES evaluation_cases (case_id),
    reviewer_id UUID NOT NULL,
    exclusive BOOLEAN NOT NULL,
    assignment_status TEXT NOT NULL CHECK (
        assignment_status IN ('assigned', 'released', 'completed')
    ),
    version INTEGER NOT NULL DEFAULT 1 CHECK (version >= 1),
    assigned_at TIMESTAMPTZ NOT NULL,
    updated_at TIMESTAMPTZ NOT NULL
);

CREATE UNIQUE INDEX reviewer_assignments_active_reviewer_idx
    ON reviewer_assignments (case_id, reviewer_id)
    WHERE assignment_status = 'assigned';

CREATE UNIQUE INDEX reviewer_assignments_exclusive_case_idx
    ON reviewer_assignments (case_id)
    WHERE assignment_status = 'assigned' AND exclusive;

CREATE TABLE adjudication_records (
    adjudication_id UUID PRIMARY KEY,
    case_id UUID NOT NULL REFERENCES evaluation_cases (case_id),
    adjudicator_id UUID NOT NULL,
    selected_revision_id UUID NOT NULL REFERENCES annotation_revisions (revision_id),
    rationale TEXT NOT NULL CHECK (length(btrim(rationale)) BETWEEN 1 AND 10000),
    created_at TIMESTAMPTZ NOT NULL
);

CREATE TABLE adjudication_considered_revisions (
    adjudication_id UUID NOT NULL REFERENCES adjudication_records (adjudication_id),
    revision_id UUID NOT NULL REFERENCES annotation_revisions (revision_id),
    PRIMARY KEY (adjudication_id, revision_id)
);

ALTER TABLE adjudication_records
    ADD CONSTRAINT adjudication_selected_considered_fk
    FOREIGN KEY (adjudication_id, selected_revision_id)
    REFERENCES adjudication_considered_revisions (adjudication_id, revision_id)
    DEFERRABLE INITIALLY DEFERRED;

CREATE INDEX annotation_documents_case_idx
    ON annotation_documents (case_id, updated_at DESC);
CREATE INDEX annotation_revisions_document_idx
    ON annotation_revisions (annotation_id, revision_number);
CREATE INDEX reviewer_assignments_case_idx
    ON reviewer_assignments (case_id, assignment_status);
