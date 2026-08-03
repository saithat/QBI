ALTER TABLE crawl_frontier
    ADD COLUMN etag TEXT CHECK (etag IS NULL OR char_length(etag) BETWEEN 1 AND 1000),
    ADD COLUMN last_modified_header TEXT CHECK (
        last_modified_header IS NULL OR char_length(last_modified_header) BETWEEN 1 AND 1000
    ),
    ADD COLUMN last_fetch_at TIMESTAMPTZ,
    ADD COLUMN last_http_status INTEGER CHECK (
        last_http_status IS NULL OR last_http_status BETWEEN 100 AND 599
    );

CREATE TABLE crawl_domain_policies (
    domain TEXT PRIMARY KEY CHECK (
        char_length(domain) BETWEEN 1 AND 253
        AND domain ~ '^[a-z0-9]([a-z0-9.-]*[a-z0-9])?$'
    ),
    minimum_interval_milliseconds INTEGER NOT NULL DEFAULT 1000 CHECK (
        minimum_interval_milliseconds BETWEEN 0 AND 3600000
    ),
    maximum_concurrency INTEGER NOT NULL DEFAULT 2 CHECK (
        maximum_concurrency BETWEEN 1 AND 1000
    ),
    next_request_at TIMESTAMPTZ NOT NULL,
    updated_at TIMESTAMPTZ NOT NULL
);

CREATE TABLE crawl_domain_permits (
    permit_id UUID PRIMARY KEY,
    domain TEXT NOT NULL REFERENCES crawl_domain_policies(domain) ON DELETE RESTRICT,
    worker_id TEXT NOT NULL CHECK (char_length(worker_id) BETWEEN 1 AND 200),
    acquired_at TIMESTAMPTZ NOT NULL,
    expires_at TIMESTAMPTZ NOT NULL,
    CHECK (expires_at > acquired_at)
);

CREATE INDEX crawl_domain_permits_expiration_idx
    ON crawl_domain_permits (domain, expires_at, permit_id);

CREATE TABLE crawl_fetch_tasks (
    task_id UUID PRIMARY KEY,
    frontier_id UUID NOT NULL REFERENCES crawl_frontier(frontier_id) ON DELETE RESTRICT,
    canonical_url TEXT NOT NULL CHECK (char_length(canonical_url) BETWEEN 1 AND 4096),
    domain TEXT NOT NULL REFERENCES crawl_domain_policies(domain) ON DELETE RESTRICT,
    acquisition_method TEXT NOT NULL CHECK (
        acquisition_method IN (
            'official_api', 'bulk_manifest', 'accession_download',
            'open_access_archive', 'controlled_crawl'
        )
    ),
    expected_media_types TEXT[] NOT NULL CHECK (cardinality(expected_media_types) > 0),
    status TEXT NOT NULL CHECK (
        status IN (
            'pending', 'leased', 'retry_wait', 'succeeded', 'not_modified',
            'prohibited', 'dead_letter', 'cancelled'
        )
    ),
    priority INTEGER NOT NULL CHECK (priority BETWEEN 0 AND 1000),
    attempt_count INTEGER NOT NULL DEFAULT 0 CHECK (attempt_count >= 0),
    max_attempts INTEGER NOT NULL CHECK (max_attempts BETWEEN 1 AND 100),
    next_eligible_at TIMESTAMPTZ NOT NULL,
    trace_id UUID NOT NULL,
    lease_owner TEXT CHECK (lease_owner IS NULL OR char_length(lease_owner) BETWEEN 1 AND 200),
    lease_token UUID UNIQUE,
    lease_expires_at TIMESTAMPTZ,
    current_attempt_id UUID UNIQUE,
    result_artifact_id UUID REFERENCES artifacts(artifact_id) ON DELETE RESTRICT,
    etag TEXT CHECK (etag IS NULL OR char_length(etag) BETWEEN 1 AND 1000),
    last_modified_header TEXT CHECK (
        last_modified_header IS NULL OR char_length(last_modified_header) BETWEEN 1 AND 1000
    ),
    last_error JSONB CHECK (last_error IS NULL OR jsonb_typeof(last_error) = 'object'),
    created_at TIMESTAMPTZ NOT NULL,
    updated_at TIMESTAMPTZ NOT NULL,
    completed_at TIMESTAMPTZ,
    CONSTRAINT crawl_fetch_task_lease_shape CHECK (
        (status = 'leased' AND lease_owner IS NOT NULL AND lease_token IS NOT NULL
            AND lease_expires_at IS NOT NULL AND current_attempt_id IS NOT NULL)
        OR
        (status <> 'leased' AND lease_owner IS NULL AND lease_token IS NULL
            AND lease_expires_at IS NULL AND current_attempt_id IS NULL)
    ),
    CONSTRAINT crawl_fetch_task_terminal_shape CHECK (
        (status IN ('succeeded', 'not_modified', 'prohibited', 'dead_letter', 'cancelled'))
        = (completed_at IS NOT NULL)
    ),
    CONSTRAINT crawl_fetch_task_result_shape CHECK (
        (status = 'succeeded') = (result_artifact_id IS NOT NULL)
    ),
    CHECK (updated_at >= created_at)
);

CREATE UNIQUE INDEX crawl_fetch_tasks_active_frontier_idx
    ON crawl_fetch_tasks (frontier_id)
    WHERE status IN ('pending', 'leased', 'retry_wait');
CREATE INDEX crawl_fetch_tasks_queue_idx
    ON crawl_fetch_tasks (priority DESC, next_eligible_at, created_at, task_id)
    WHERE status IN ('pending', 'retry_wait');
CREATE INDEX crawl_fetch_tasks_status_idx
    ON crawl_fetch_tasks (status, updated_at DESC, task_id);
CREATE INDEX crawl_fetch_tasks_domain_idx
    ON crawl_fetch_tasks (domain, status, updated_at DESC, task_id);

CREATE TABLE crawl_fetch_attempts (
    attempt_id UUID PRIMARY KEY,
    task_id UUID NOT NULL REFERENCES crawl_fetch_tasks(task_id) ON DELETE RESTRICT,
    frontier_id UUID NOT NULL REFERENCES crawl_frontier(frontier_id) ON DELETE RESTRICT,
    attempt_number INTEGER NOT NULL CHECK (attempt_number >= 1),
    worker_id TEXT NOT NULL CHECK (char_length(worker_id) BETWEEN 1 AND 200),
    lease_token UUID NOT NULL UNIQUE,
    trace_id UUID NOT NULL,
    outcome TEXT NOT NULL CHECK (
        outcome IN (
            'leased', 'succeeded', 'not_modified', 'retryable_failure',
            'permanent_failure', 'prohibited', 'lease_expired', 'cancelled'
        )
    ),
    request_url TEXT NOT NULL CHECK (char_length(request_url) BETWEEN 1 AND 4096),
    final_url TEXT CHECK (final_url IS NULL OR char_length(final_url) BETWEEN 1 AND 4096),
    http_status INTEGER CHECK (http_status IS NULL OR http_status BETWEEN 100 AND 599),
    response_media_type TEXT CHECK (
        response_media_type IS NULL
        OR response_media_type ~ '^[a-z0-9][a-z0-9!#$&^_.+-]*/'
    ),
    response_sha256 CHAR(64) CHECK (
        response_sha256 IS NULL OR response_sha256 ~ '^[a-f0-9]{64}$'
    ),
    bytes_downloaded BIGINT CHECK (bytes_downloaded IS NULL OR bytes_downloaded >= 0),
    latency_milliseconds BIGINT CHECK (
        latency_milliseconds IS NULL OR latency_milliseconds >= 0
    ),
    artifact_id UUID REFERENCES artifacts(artifact_id) ON DELETE RESTRICT,
    artifact_deduplicated BOOLEAN,
    retry_after TIMESTAMPTZ,
    error_json JSONB CHECK (error_json IS NULL OR jsonb_typeof(error_json) = 'object'),
    leased_at TIMESTAMPTZ NOT NULL,
    started_at TIMESTAMPTZ,
    completed_at TIMESTAMPTZ,
    UNIQUE (task_id, attempt_number),
    CONSTRAINT crawl_fetch_attempt_terminal_shape CHECK (
        (outcome <> 'leased') = (completed_at IS NOT NULL)
    )
);

CREATE INDEX crawl_fetch_attempts_metrics_idx
    ON crawl_fetch_attempts (completed_at, outcome, http_status, task_id);
CREATE INDEX crawl_fetch_attempts_frontier_idx
    ON crawl_fetch_attempts (frontier_id, attempt_number DESC, attempt_id);

CREATE TABLE crawl_robots_cache (
    origin TEXT PRIMARY KEY CHECK (char_length(origin) BETWEEN 1 AND 4096),
    domain TEXT NOT NULL REFERENCES crawl_domain_policies(domain) ON DELETE RESTRICT,
    http_status INTEGER NOT NULL CHECK (http_status BETWEEN 100 AND 599),
    rules_text TEXT CHECK (rules_text IS NULL OR octet_length(rules_text) <= 524288),
    artifact_id UUID REFERENCES artifacts(artifact_id) ON DELETE RESTRICT,
    fetched_at TIMESTAMPTZ NOT NULL,
    expires_at TIMESTAMPTZ NOT NULL,
    etag TEXT CHECK (etag IS NULL OR char_length(etag) BETWEEN 1 AND 1000),
    last_modified_header TEXT CHECK (
        last_modified_header IS NULL OR char_length(last_modified_header) BETWEEN 1 AND 1000
    ),
    CHECK (expires_at > fetched_at)
);

CREATE INDEX crawl_robots_cache_expiration_idx
    ON crawl_robots_cache (expires_at, origin);

CREATE TABLE crawl_downstream_tasks (
    downstream_task_id UUID PRIMARY KEY,
    fetch_task_id UUID NOT NULL REFERENCES crawl_fetch_tasks(task_id) ON DELETE RESTRICT,
    frontier_id UUID NOT NULL REFERENCES crawl_frontier(frontier_id) ON DELETE RESTRICT,
    task_kind TEXT NOT NULL CHECK (task_kind IN ('parse', 'extract')),
    status TEXT NOT NULL CHECK (status IN ('pending', 'published', 'failed')),
    artifact_id UUID NOT NULL REFERENCES artifacts(artifact_id) ON DELETE RESTRICT,
    trace_id UUID NOT NULL,
    attempt_count INTEGER NOT NULL DEFAULT 0 CHECK (attempt_count >= 0),
    last_error TEXT CHECK (last_error IS NULL OR char_length(last_error) BETWEEN 1 AND 4000),
    created_at TIMESTAMPTZ NOT NULL,
    published_at TIMESTAMPTZ,
    UNIQUE (fetch_task_id, task_kind),
    CONSTRAINT crawl_downstream_publication_shape CHECK (
        (status = 'published') = (published_at IS NOT NULL)
    )
);

CREATE INDEX crawl_downstream_tasks_queue_idx
    ON crawl_downstream_tasks (created_at, downstream_task_id)
    WHERE status = 'pending';
