CREATE TABLE evaluation_metric_runs (
    metric_run_id UUID PRIMARY KEY,
    dataset_snapshot_id UUID NOT NULL,
    dataset_name TEXT NOT NULL CHECK (length(btrim(dataset_name)) BETWEEN 1 AND 200),
    dataset_version TEXT NOT NULL CHECK (length(btrim(dataset_version)) BETWEEN 1 AND 200),
    dataset_sha256 TEXT NOT NULL CHECK (dataset_sha256 ~ '^[a-f0-9]{64}$'),
    scorer_name TEXT NOT NULL CHECK (length(btrim(scorer_name)) BETWEEN 1 AND 200),
    scorer_version TEXT NOT NULL CHECK (length(btrim(scorer_version)) BETWEEN 1 AND 200),
    pipeline_name TEXT NOT NULL CHECK (length(btrim(pipeline_name)) BETWEEN 1 AND 200),
    pipeline_version TEXT NOT NULL CHECK (length(btrim(pipeline_version)) BETWEEN 1 AND 200),
    input_sha256 TEXT NOT NULL CHECK (input_sha256 ~ '^[a-f0-9]{64}$'),
    configuration_json JSONB NOT NULL CHECK (jsonb_typeof(configuration_json) = 'object'),
    metric_run_json JSONB NOT NULL CHECK (jsonb_typeof(metric_run_json) = 'object'),
    created_at TIMESTAMPTZ NOT NULL
);

CREATE TABLE evaluation_metric_case_results (
    metric_run_id UUID NOT NULL REFERENCES evaluation_metric_runs (metric_run_id),
    case_id UUID NOT NULL,
    composite_score DOUBLE PRECISION NOT NULL CHECK (
        composite_score >= 0 AND composite_score <= 1
    ),
    case_result_json JSONB NOT NULL CHECK (jsonb_typeof(case_result_json) = 'object'),
    PRIMARY KEY (metric_run_id, case_id)
);

CREATE INDEX evaluation_metric_runs_dataset_idx
    ON evaluation_metric_runs (
        dataset_name, dataset_version, dataset_sha256, created_at DESC, metric_run_id
    );
CREATE INDEX evaluation_metric_runs_pipeline_idx
    ON evaluation_metric_runs (
        pipeline_name, pipeline_version, created_at DESC, metric_run_id
    );
CREATE INDEX evaluation_metric_case_results_case_idx
    ON evaluation_metric_case_results (case_id, metric_run_id);
