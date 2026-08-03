CREATE TABLE discovery_runs (
    batch_id UUID PRIMARY KEY,
    source_name TEXT NOT NULL CHECK (char_length(source_name) BETWEEN 1 AND 200),
    source_version TEXT NOT NULL CHECK (char_length(source_version) BETWEEN 1 AND 200),
    trace_id UUID NOT NULL,
    query_json JSONB NOT NULL CHECK (jsonb_typeof(query_json) = 'object'),
    batch_json JSONB NOT NULL CHECK (jsonb_typeof(batch_json) = 'object'),
    batch_sha256 CHAR(64) NOT NULL CHECK (batch_sha256 ~ '^[a-f0-9]{64}$'),
    started_at TIMESTAMPTZ NOT NULL,
    completed_at TIMESTAMPTZ NOT NULL,
    created_count INTEGER NOT NULL DEFAULT 0 CHECK (created_count >= 0),
    deduplicated_count INTEGER NOT NULL DEFAULT 0 CHECK (deduplicated_count >= 0),
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    CHECK (completed_at >= started_at)
);

CREATE INDEX discovery_runs_source_time_idx
    ON discovery_runs (source_name, completed_at DESC, batch_id);
CREATE INDEX discovery_runs_trace_idx ON discovery_runs (trace_id, batch_id);

CREATE TABLE discovery_run_evidence (
    batch_id UUID NOT NULL REFERENCES discovery_runs(batch_id) ON DELETE RESTRICT,
    sequence INTEGER NOT NULL CHECK (sequence >= 1),
    artifact_id UUID NOT NULL REFERENCES artifacts(artifact_id) ON DELETE RESTRICT,
    request_url TEXT NOT NULL CHECK (char_length(request_url) BETWEEN 1 AND 4096),
    response_sha256 CHAR(64) NOT NULL CHECK (response_sha256 ~ '^[a-f0-9]{64}$'),
    response_media_type TEXT NOT NULL CHECK (
        response_media_type ~ '^[a-z0-9][a-z0-9!#$&^_.+-]*/'
    ),
    http_status INTEGER NOT NULL CHECK (http_status BETWEEN 100 AND 599),
    fetched_at TIMESTAMPTZ NOT NULL,
    PRIMARY KEY (batch_id, sequence)
);

CREATE INDEX discovery_run_evidence_artifact_idx
    ON discovery_run_evidence (artifact_id, batch_id, sequence);

CREATE TABLE crawl_frontier (
    frontier_id UUID PRIMARY KEY,
    identity_key TEXT NOT NULL UNIQUE CHECK (
        char_length(identity_key) BETWEEN 1 AND 400
        AND identity_key ~ '^[a-z0-9][a-z0-9:._/-]*$'
    ),
    primary_source_name TEXT NOT NULL CHECK (char_length(primary_source_name) BETWEEN 1 AND 200),
    primary_source_version TEXT NOT NULL
        CHECK (char_length(primary_source_version) BETWEEN 1 AND 200),
    source_record_id TEXT NOT NULL CHECK (char_length(source_record_id) BETWEEN 1 AND 200),
    accession TEXT CHECK (accession IS NULL OR char_length(accession) BETWEEN 1 AND 200),
    entity_kind TEXT NOT NULL CHECK (
        entity_kind IN ('paper', 'supplementary_archive', 'source_data_image', 'metadata_record')
    ),
    canonical_url TEXT NOT NULL UNIQUE CHECK (char_length(canonical_url) BETWEEN 1 AND 4096),
    acquisition_method TEXT NOT NULL CHECK (
        acquisition_method IN (
            'official_api', 'bulk_manifest', 'accession_download',
            'open_access_archive', 'controlled_crawl'
        )
    ),
    status TEXT NOT NULL CHECK (
        status IN (
            'pending', 'leased', 'acquired', 'retry_wait',
            'prohibited', 'unsupported', 'dead_letter'
        )
    ),
    priority INTEGER NOT NULL CHECK (priority BETWEEN 0 AND 1000),
    source_updated_at TIMESTAMPTZ,
    discovered_at TIMESTAMPTZ NOT NULL,
    last_discovered_at TIMESTAMPTZ NOT NULL,
    next_eligible_fetch_at TIMESTAMPTZ NOT NULL,
    attempt_count INTEGER NOT NULL DEFAULT 0 CHECK (attempt_count >= 0),
    lease_owner TEXT CHECK (lease_owner IS NULL OR char_length(lease_owner) BETWEEN 1 AND 200),
    lease_token UUID UNIQUE,
    lease_expires_at TIMESTAMPTZ,
    robots_status TEXT NOT NULL CHECK (
        robots_status IN ('not_applicable', 'unknown', 'allowed', 'prohibited')
    ),
    expected_media_types TEXT[] NOT NULL DEFAULT ARRAY[]::TEXT[]
        CHECK (cardinality(expected_media_types) <= 20),
    license_json JSONB NOT NULL CHECK (jsonb_typeof(license_json) = 'object'),
    access_status TEXT NOT NULL CHECK (
        access_status IN ('allowed', 'restricted', 'prohibited', 'unknown')
    ),
    access_reason TEXT CHECK (
        access_reason IS NULL OR char_length(access_reason) BETWEEN 1 AND 2000
    ),
    trace_id UUID NOT NULL,
    last_discovery_batch_id UUID NOT NULL REFERENCES discovery_runs(batch_id) ON DELETE RESTRICT,
    version INTEGER NOT NULL DEFAULT 1 CHECK (version >= 1),
    created_at TIMESTAMPTZ NOT NULL,
    updated_at TIMESTAMPTZ NOT NULL,
    CONSTRAINT crawl_frontier_time_order CHECK (
        last_discovered_at >= discovered_at AND updated_at >= created_at
    ),
    CONSTRAINT crawl_frontier_lease_shape CHECK (
        (status = 'leased' AND lease_owner IS NOT NULL
            AND lease_token IS NOT NULL AND lease_expires_at IS NOT NULL)
        OR
        (status <> 'leased' AND lease_owner IS NULL
            AND lease_token IS NULL AND lease_expires_at IS NULL)
    ),
    CONSTRAINT crawl_frontier_prohibited_shape CHECK (
        (status = 'prohibited' AND access_status = 'prohibited' AND access_reason IS NOT NULL)
        OR
        (status <> 'prohibited' AND access_status <> 'prohibited')
    ),
    CONSTRAINT crawl_frontier_supported_shape CHECK (
        status IN ('prohibited', 'unsupported') OR cardinality(expected_media_types) > 0
    )
);

CREATE INDEX crawl_frontier_eligible_idx
    ON crawl_frontier (priority DESC, next_eligible_fetch_at, discovered_at, frontier_id)
    WHERE status = 'pending';
CREATE INDEX crawl_frontier_status_updated_idx
    ON crawl_frontier (status, updated_at DESC, frontier_id);
CREATE INDEX crawl_frontier_source_idx
    ON crawl_frontier (primary_source_name, entity_kind, updated_at DESC, frontier_id);
CREATE INDEX crawl_frontier_trace_idx ON crawl_frontier (trace_id, frontier_id);

CREATE TABLE crawl_frontier_aliases (
    identity_key TEXT PRIMARY KEY CHECK (
        char_length(identity_key) BETWEEN 1 AND 400
        AND identity_key ~ '^[a-z0-9][a-z0-9:._/-]*$'
    ),
    frontier_id UUID NOT NULL REFERENCES crawl_frontier(frontier_id) ON DELETE RESTRICT,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE INDEX crawl_frontier_aliases_frontier_idx
    ON crawl_frontier_aliases (frontier_id, identity_key);

CREATE TABLE discovery_observations (
    batch_id UUID NOT NULL REFERENCES discovery_runs(batch_id) ON DELETE RESTRICT,
    frontier_id UUID NOT NULL REFERENCES crawl_frontier(frontier_id) ON DELETE RESTRICT,
    candidate_identity_key TEXT NOT NULL,
    candidate_json JSONB NOT NULL CHECK (jsonb_typeof(candidate_json) = 'object'),
    observed_at TIMESTAMPTZ NOT NULL,
    PRIMARY KEY (batch_id, candidate_identity_key),
    FOREIGN KEY (candidate_identity_key)
        REFERENCES crawl_frontier_aliases(identity_key) ON DELETE RESTRICT
);

CREATE INDEX discovery_observations_frontier_idx
    ON discovery_observations (frontier_id, observed_at DESC, batch_id);

CREATE TABLE crawl_frontier_relationships (
    frontier_id UUID NOT NULL REFERENCES crawl_frontier(frontier_id) ON DELETE RESTRICT,
    related_frontier_id UUID NOT NULL REFERENCES crawl_frontier(frontier_id) ON DELETE RESTRICT,
    relationship_kind TEXT NOT NULL CHECK (
        relationship_kind IN (
            'paper_has_supplement', 'paper_has_source_data',
            'describes', 'alternative_representation'
        )
    ),
    created_at TIMESTAMPTZ NOT NULL,
    PRIMARY KEY (frontier_id, related_frontier_id, relationship_kind),
    CHECK (frontier_id <> related_frontier_id)
);

CREATE INDEX crawl_frontier_relationship_target_idx
    ON crawl_frontier_relationships (related_frontier_id, frontier_id);

CREATE TABLE crawl_frontier_artifacts (
    frontier_id UUID NOT NULL REFERENCES crawl_frontier(frontier_id) ON DELETE RESTRICT,
    artifact_id UUID NOT NULL REFERENCES artifacts(artifact_id) ON DELETE RESTRICT,
    trace_id UUID NOT NULL,
    acquired_at TIMESTAMPTZ NOT NULL,
    PRIMARY KEY (frontier_id, artifact_id)
);

CREATE INDEX crawl_frontier_artifacts_artifact_idx
    ON crawl_frontier_artifacts (artifact_id, frontier_id);

CREATE TABLE crawl_frontier_events (
    event_id BIGINT GENERATED BY DEFAULT AS IDENTITY PRIMARY KEY,
    frontier_id UUID NOT NULL REFERENCES crawl_frontier(frontier_id) ON DELETE RESTRICT,
    event_type TEXT NOT NULL CHECK (
        event_type IN ('discovered', 'rediscovered', 'schedule_updated', 'manual_retry', 'artifact_linked')
    ),
    trace_id UUID,
    details JSONB NOT NULL DEFAULT '{}'::JSONB CHECK (jsonb_typeof(details) = 'object'),
    created_at TIMESTAMPTZ NOT NULL
);

CREATE INDEX crawl_frontier_events_record_idx
    ON crawl_frontier_events (frontier_id, created_at DESC, event_id DESC);
