CREATE TABLE pipeline_definitions (
    definition_id UUID PRIMARY KEY,
    pipeline_name TEXT NOT NULL CHECK (length(btrim(pipeline_name)) BETWEEN 1 AND 200),
    pipeline_version TEXT NOT NULL CHECK (length(btrim(pipeline_version)) BETWEEN 1 AND 200),
    definition_json JSONB NOT NULL CHECK (jsonb_typeof(definition_json) = 'object'),
    created_at TIMESTAMPTZ NOT NULL,
    UNIQUE (pipeline_name, pipeline_version)
);

CREATE TABLE pipeline_runs (
    run_id UUID PRIMARY KEY,
    case_id UUID NOT NULL REFERENCES evaluation_cases (case_id),
    definition_id UUID NOT NULL REFERENCES pipeline_definitions (definition_id),
    run_status TEXT NOT NULL CHECK (run_status IN ('active', 'published')),
    run_json JSONB NOT NULL CHECK (jsonb_typeof(run_json) = 'object'),
    trace_id UUID NOT NULL,
    created_at TIMESTAMPTZ NOT NULL,
    updated_at TIMESTAMPTZ NOT NULL CHECK (updated_at >= created_at),
    UNIQUE (run_id, case_id)
);

CREATE TABLE pipeline_run_input_artifacts (
    run_id UUID NOT NULL REFERENCES pipeline_runs (run_id),
    position INTEGER NOT NULL CHECK (position >= 0),
    artifact_id UUID NOT NULL REFERENCES artifacts (artifact_id),
    PRIMARY KEY (run_id, position),
    UNIQUE (run_id, artifact_id)
);

CREATE TABLE component_invocations (
    invocation_id UUID PRIMARY KEY,
    run_id UUID NOT NULL REFERENCES pipeline_runs (run_id),
    component_key TEXT NOT NULL CHECK (length(btrim(component_key)) BETWEEN 1 AND 200),
    invocation_status TEXT NOT NULL CHECK (
        invocation_status IN ('pending', 'succeeded', 'failed')
    ),
    replay_of_invocation_id UUID,
    invocation_json JSONB NOT NULL CHECK (jsonb_typeof(invocation_json) = 'object'),
    trace_id UUID NOT NULL,
    created_at TIMESTAMPTZ NOT NULL,
    completed_at TIMESTAMPTZ CHECK (completed_at IS NULL OR completed_at >= created_at),
    UNIQUE (run_id, invocation_id),
    CHECK (
        (invocation_status = 'pending' AND completed_at IS NULL)
        OR (invocation_status IN ('succeeded', 'failed') AND completed_at IS NOT NULL)
    ),
    CHECK (replay_of_invocation_id IS NULL OR replay_of_invocation_id <> invocation_id),
    FOREIGN KEY (run_id, replay_of_invocation_id)
        REFERENCES component_invocations (run_id, invocation_id)
        DEFERRABLE INITIALLY DEFERRED
);

CREATE TABLE component_invocation_parents (
    run_id UUID NOT NULL,
    invocation_id UUID NOT NULL,
    position INTEGER NOT NULL CHECK (position >= 0),
    parent_invocation_id UUID NOT NULL,
    PRIMARY KEY (invocation_id, position),
    UNIQUE (invocation_id, parent_invocation_id),
    CHECK (invocation_id <> parent_invocation_id),
    FOREIGN KEY (run_id, invocation_id)
        REFERENCES component_invocations (run_id, invocation_id),
    FOREIGN KEY (run_id, parent_invocation_id)
        REFERENCES component_invocations (run_id, invocation_id)
);

CREATE TABLE component_invocation_input_artifacts (
    invocation_id UUID NOT NULL REFERENCES component_invocations (invocation_id),
    position INTEGER NOT NULL CHECK (position >= 0),
    artifact_id UUID NOT NULL REFERENCES artifacts (artifact_id),
    PRIMARY KEY (invocation_id, position),
    UNIQUE (invocation_id, artifact_id)
);

CREATE TABLE component_invocation_output_artifacts (
    invocation_id UUID NOT NULL REFERENCES component_invocations (invocation_id),
    position INTEGER NOT NULL CHECK (position >= 0),
    artifact_id UUID NOT NULL REFERENCES artifacts (artifact_id),
    PRIMARY KEY (invocation_id, position),
    UNIQUE (invocation_id, artifact_id)
);

CREATE TABLE component_invocation_evidence_artifacts (
    invocation_id UUID NOT NULL REFERENCES component_invocations (invocation_id),
    artifact_id UUID NOT NULL REFERENCES artifacts (artifact_id),
    PRIMARY KEY (invocation_id, artifact_id)
);

CREATE TABLE pipeline_publications (
    publication_id UUID PRIMARY KEY,
    run_id UUID NOT NULL,
    case_id UUID NOT NULL,
    publication_json JSONB NOT NULL CHECK (jsonb_typeof(publication_json) = 'object'),
    trace_id UUID NOT NULL,
    created_at TIMESTAMPTZ NOT NULL,
    FOREIGN KEY (run_id, case_id) REFERENCES pipeline_runs (run_id, case_id)
);

CREATE TABLE pipeline_publication_output_artifacts (
    publication_id UUID NOT NULL REFERENCES pipeline_publications (publication_id),
    position INTEGER NOT NULL CHECK (position >= 0),
    artifact_id UUID NOT NULL REFERENCES artifacts (artifact_id),
    PRIMARY KEY (publication_id, position),
    UNIQUE (publication_id, artifact_id)
);

CREATE INDEX pipeline_runs_case_time_idx
    ON pipeline_runs (case_id, created_at DESC, run_id);
CREATE INDEX component_invocations_run_time_idx
    ON component_invocations (run_id, created_at, invocation_id);
CREATE INDEX component_invocations_status_idx
    ON component_invocations (invocation_status, created_at);
CREATE INDEX pipeline_publications_run_time_idx
    ON pipeline_publications (run_id, created_at, publication_id);
