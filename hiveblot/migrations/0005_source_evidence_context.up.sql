CREATE TABLE evaluation_case_source_contexts (
    context_revision_id UUID PRIMARY KEY,
    case_id UUID NOT NULL,
    artifact_id UUID NOT NULL,
    artifact_role TEXT NOT NULL,
    revision_number INTEGER NOT NULL CHECK (revision_number >= 1),
    prior_revision_id UUID,
    caption TEXT CHECK (caption IS NULL OR length(caption) <= 50000),
    nearby_text TEXT CHECK (nearby_text IS NULL OR length(nearby_text) <= 100000),
    created_at TIMESTAMPTZ NOT NULL,
    UNIQUE (case_id, artifact_id, artifact_role, revision_number),
    UNIQUE (case_id, artifact_id, artifact_role, context_revision_id),
    FOREIGN KEY (case_id, artifact_id, artifact_role)
        REFERENCES evaluation_case_artifacts (case_id, artifact_id, artifact_role),
    FOREIGN KEY (case_id, artifact_id, artifact_role, prior_revision_id)
        REFERENCES evaluation_case_source_contexts (
            case_id, artifact_id, artifact_role, context_revision_id
        ) DEFERRABLE INITIALLY DEFERRED,
    CHECK (
        (revision_number = 1 AND prior_revision_id IS NULL)
        OR (revision_number > 1 AND prior_revision_id IS NOT NULL)
    ),
    CHECK (
        NULLIF(btrim(COALESCE(caption, '')), '') IS NOT NULL
        OR NULLIF(btrim(COALESCE(nearby_text, '')), '') IS NOT NULL
    )
);

CREATE INDEX evaluation_case_source_contexts_case_idx
    ON evaluation_case_source_contexts (
        case_id, artifact_role, artifact_id, revision_number DESC
    );
