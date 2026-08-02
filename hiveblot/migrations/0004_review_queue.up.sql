CREATE TABLE evaluation_case_review_metadata (
    case_id UUID PRIMARY KEY REFERENCES evaluation_cases (case_id),
    gold_eligible BOOLEAN NOT NULL DEFAULT FALSE,
    regression_status TEXT NOT NULL DEFAULT 'not_evaluated' CHECK (
        regression_status IN ('not_evaluated', 'stable', 'improved', 'regressed')
    ),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE saved_review_views (
    view_id UUID PRIMARY KEY,
    owner_id UUID NOT NULL,
    name TEXT NOT NULL CHECK (length(btrim(name)) BETWEEN 1 AND 200),
    filters_json JSONB NOT NULL CHECK (jsonb_typeof(filters_json) = 'object'),
    version INTEGER NOT NULL DEFAULT 1 CHECK (version >= 1),
    created_at TIMESTAMPTZ NOT NULL,
    updated_at TIMESTAMPTZ NOT NULL,
    UNIQUE (owner_id, name)
);

CREATE INDEX evaluation_cases_review_queue_idx
    ON evaluation_cases (review_status, updated_at DESC, case_id);
CREATE INDEX evaluation_cases_dataset_queue_idx
    ON evaluation_cases (dataset_id, updated_at DESC, case_id)
    WHERE dataset_id IS NOT NULL;
CREATE INDEX prediction_documents_version_queue_idx
    ON prediction_documents (prediction_schema_version, case_id, created_at DESC);
CREATE INDEX reviewer_assignments_reviewer_queue_idx
    ON reviewer_assignments (reviewer_id, case_id)
    WHERE assignment_status = 'assigned';
CREATE INDEX annotation_error_codes_category_idx
    ON annotation_error_codes (category, code);
CREATE INDEX evaluation_case_review_metadata_gold_idx
    ON evaluation_case_review_metadata (gold_eligible, regression_status, case_id);
CREATE INDEX saved_review_views_owner_idx
    ON saved_review_views (owner_id, updated_at DESC, view_id);
