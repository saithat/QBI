CREATE TABLE jobs (
    job_id UUID PRIMARY KEY,
    job_type TEXT NOT NULL,
    idempotency_key TEXT NOT NULL,
    parent_job_id UUID REFERENCES jobs(job_id) ON DELETE RESTRICT,
    trace_id UUID NOT NULL,
    specification JSONB NOT NULL,
    specification_sha256 CHAR(64) NOT NULL,
    status TEXT NOT NULL CHECK (
        status IN ('pending', 'leased', 'running', 'succeeded', 'failed', 'cancelled', 'dead_letter')
    ),
    attempt_count INTEGER NOT NULL DEFAULT 0 CHECK (attempt_count >= 0),
    cancel_requested BOOLEAN NOT NULL DEFAULT FALSE,
    cancellation_reason TEXT,
    next_eligible_at TIMESTAMPTZ NOT NULL,
    lease_owner TEXT,
    lease_token UUID UNIQUE,
    lease_expires_at TIMESTAMPTZ,
    result JSONB,
    created_at TIMESTAMPTZ NOT NULL,
    updated_at TIMESTAMPTZ NOT NULL,
    completed_at TIMESTAMPTZ,
    CONSTRAINT jobs_idempotency_key UNIQUE (job_type, idempotency_key),
    CONSTRAINT jobs_parent_not_self CHECK (parent_job_id IS NULL OR parent_job_id <> job_id),
    CONSTRAINT jobs_specification_sha256 CHECK (
        specification_sha256 ~ '^[a-f0-9]{64}$'
    ),
    CONSTRAINT jobs_cancellation_reason_length CHECK (
        cancellation_reason IS NULL OR char_length(cancellation_reason) <= 4000
    ),
    CONSTRAINT jobs_lease_owner_length CHECK (
        lease_owner IS NULL OR char_length(lease_owner) BETWEEN 1 AND 200
    ),
    CONSTRAINT jobs_cancel_shape CHECK (
        cancel_requested = (cancellation_reason IS NOT NULL)
    ),
    CONSTRAINT jobs_lease_shape CHECK (
        (status IN ('leased', 'running') AND lease_owner IS NOT NULL
            AND lease_token IS NOT NULL AND lease_expires_at IS NOT NULL)
        OR
        (status NOT IN ('leased', 'running') AND lease_owner IS NULL
            AND lease_token IS NULL AND lease_expires_at IS NULL)
    ),
    CONSTRAINT jobs_terminal_shape CHECK (
        (status IN ('succeeded', 'failed', 'cancelled', 'dead_letter')
            AND completed_at IS NOT NULL)
        OR
        (status NOT IN ('succeeded', 'failed', 'cancelled', 'dead_letter')
            AND completed_at IS NULL)
    )
);

CREATE INDEX jobs_leaseable_idx
    ON jobs (next_eligible_at, created_at, job_id)
    WHERE status = 'pending' AND cancel_requested = FALSE;

CREATE INDEX jobs_status_updated_idx ON jobs (status, updated_at DESC, job_id);
CREATE INDEX jobs_parent_idx ON jobs (parent_job_id, created_at, job_id)
    WHERE parent_job_id IS NOT NULL;
CREATE INDEX jobs_trace_idx ON jobs (trace_id, created_at, job_id);

CREATE TABLE job_attempts (
    attempt_id UUID PRIMARY KEY,
    job_id UUID NOT NULL REFERENCES jobs(job_id) ON DELETE RESTRICT,
    attempt_number INTEGER NOT NULL CHECK (attempt_number >= 1),
    worker_id TEXT NOT NULL,
    lease_token UUID NOT NULL UNIQUE,
    status TEXT NOT NULL CHECK (
        status IN ('leased', 'running', 'succeeded', 'failed', 'cancelled', 'lease_expired')
    ),
    executor_name TEXT NOT NULL,
    leased_at TIMESTAMPTZ NOT NULL,
    started_at TIMESTAMPTZ,
    completed_at TIMESTAMPTZ,
    lease_expires_at TIMESTAMPTZ NOT NULL,
    result JSONB,
    CONSTRAINT job_attempt_number UNIQUE (job_id, attempt_number),
    CONSTRAINT job_attempt_identity UNIQUE (job_id, attempt_id),
    CONSTRAINT job_attempt_worker_length CHECK (char_length(worker_id) BETWEEN 1 AND 200),
    CONSTRAINT job_attempt_executor_length CHECK (char_length(executor_name) BETWEEN 1 AND 200),
    CONSTRAINT job_attempt_terminal_shape CHECK (
        (status IN ('succeeded', 'failed', 'cancelled', 'lease_expired')
            AND completed_at IS NOT NULL)
        OR
        (status IN ('leased', 'running') AND completed_at IS NULL)
    )
);

CREATE INDEX job_attempts_job_idx
    ON job_attempts (job_id, attempt_number, attempt_id);

CREATE TABLE job_logs (
    log_id UUID PRIMARY KEY,
    attempt_id UUID NOT NULL REFERENCES job_attempts(attempt_id) ON DELETE RESTRICT,
    sequence INTEGER NOT NULL CHECK (sequence >= 1),
    stream TEXT NOT NULL CHECK (stream IN ('stdout', 'stderr', 'system')),
    content TEXT NOT NULL,
    truncated BOOLEAN NOT NULL DEFAULT FALSE,
    created_at TIMESTAMPTZ NOT NULL,
    CONSTRAINT job_log_sequence UNIQUE (attempt_id, stream, sequence),
    CONSTRAINT job_log_content_length CHECK (char_length(content) <= 262144)
);

CREATE INDEX job_logs_attempt_idx
    ON job_logs (attempt_id, sequence, stream, log_id);

CREATE TABLE job_outputs (
    job_id UUID NOT NULL,
    output_name TEXT NOT NULL,
    attempt_id UUID NOT NULL,
    artifact_id UUID NOT NULL REFERENCES artifacts(artifact_id) ON DELETE RESTRICT,
    created_at TIMESTAMPTZ NOT NULL,
    PRIMARY KEY (job_id, output_name),
    CONSTRAINT job_output_name CHECK (output_name ~ '^[a-z][a-z0-9_.-]{0,127}$'),
    CONSTRAINT job_output_attempt FOREIGN KEY (job_id, attempt_id)
        REFERENCES job_attempts(job_id, attempt_id) ON DELETE RESTRICT
);

CREATE INDEX job_outputs_artifact_idx ON job_outputs (artifact_id, job_id);
