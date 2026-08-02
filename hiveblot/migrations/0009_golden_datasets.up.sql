CREATE TABLE golden_datasets (
    dataset_id UUID PRIMARY KEY,
    dataset_name TEXT NOT NULL CHECK (length(btrim(dataset_name)) BETWEEN 1 AND 200),
    dataset_version TEXT NOT NULL CHECK (length(btrim(dataset_version)) BETWEEN 1 AND 200),
    dataset_type TEXT NOT NULL CHECK (
        dataset_type IN (
            'development', 'frozen_test', 'challenge', 'shadow',
            'densitometry_reference', 'retrieval_evaluation'
        )
    ),
    dataset_status TEXT NOT NULL CHECK (dataset_status IN ('draft', 'frozen')),
    predecessor_snapshot_id UUID,
    revision INTEGER NOT NULL CHECK (revision >= 1),
    created_by UUID NOT NULL,
    dataset_json JSONB NOT NULL CHECK (jsonb_typeof(dataset_json) = 'object'),
    created_at TIMESTAMPTZ NOT NULL,
    updated_at TIMESTAMPTZ NOT NULL CHECK (updated_at >= created_at),
    UNIQUE (dataset_name, dataset_version)
);

CREATE TABLE golden_dataset_papers (
    dataset_id UUID NOT NULL REFERENCES golden_datasets (dataset_id) ON DELETE CASCADE,
    paper_key TEXT NOT NULL CHECK (length(btrim(paper_key)) BETWEEN 1 AND 200),
    split TEXT NOT NULL CHECK (split IN ('train', 'validation', 'test')),
    PRIMARY KEY (dataset_id, paper_key),
    UNIQUE (dataset_id, paper_key, split)
);

CREATE TABLE golden_dataset_members (
    dataset_id UUID NOT NULL REFERENCES golden_datasets (dataset_id) ON DELETE CASCADE,
    case_id UUID NOT NULL REFERENCES evaluation_cases (case_id),
    paper_key TEXT NOT NULL,
    split TEXT NOT NULL CHECK (split IN ('train', 'validation', 'test')),
    case_state TEXT NOT NULL CHECK (
        case_state IN (
            'unlabeled', 'predicted', 'reviewed', 'needs_adjudication',
            'adjudicated', 'gold_candidate', 'gold', 'retired'
        )
    ),
    selected_revision_id UUID REFERENCES annotation_revisions (revision_id),
    state_version INTEGER NOT NULL CHECK (state_version >= 1),
    added_by UUID NOT NULL,
    member_json JSONB NOT NULL CHECK (jsonb_typeof(member_json) = 'object'),
    added_at TIMESTAMPTZ NOT NULL,
    updated_at TIMESTAMPTZ NOT NULL CHECK (updated_at >= added_at),
    PRIMARY KEY (dataset_id, case_id),
    FOREIGN KEY (dataset_id, paper_key, split)
        REFERENCES golden_dataset_papers (dataset_id, paper_key, split),
    CHECK (
        case_state NOT IN (
            'reviewed', 'needs_adjudication', 'adjudicated', 'gold_candidate', 'gold'
        ) OR selected_revision_id IS NOT NULL
    )
);

CREATE TABLE golden_dataset_content_hashes (
    dataset_id UUID NOT NULL REFERENCES golden_datasets (dataset_id) ON DELETE CASCADE,
    sha256 TEXT NOT NULL CHECK (sha256 ~ '^[a-f0-9]{64}$'),
    split TEXT NOT NULL CHECK (split IN ('train', 'validation', 'test')),
    PRIMARY KEY (dataset_id, sha256),
    UNIQUE (dataset_id, sha256, split)
);

CREATE TABLE golden_dataset_member_artifacts (
    dataset_id UUID NOT NULL,
    case_id UUID NOT NULL,
    artifact_id UUID NOT NULL REFERENCES artifacts (artifact_id),
    artifact_role TEXT NOT NULL CHECK (
        artifact_role IN ('source_document', 'figure', 'raw_source', 'supplementary', 'context')
    ),
    page_number INTEGER CHECK (page_number IS NULL OR page_number >= 1),
    sha256 TEXT NOT NULL CHECK (sha256 ~ '^[a-f0-9]{64}$'),
    split TEXT NOT NULL CHECK (split IN ('train', 'validation', 'test')),
    PRIMARY KEY (dataset_id, case_id, artifact_id, artifact_role),
    FOREIGN KEY (dataset_id, case_id)
        REFERENCES golden_dataset_members (dataset_id, case_id) ON DELETE CASCADE,
    FOREIGN KEY (dataset_id, sha256, split)
        REFERENCES golden_dataset_content_hashes (dataset_id, sha256, split)
);

CREATE TABLE golden_case_transitions (
    transition_id UUID PRIMARY KEY,
    dataset_id UUID NOT NULL,
    case_id UUID NOT NULL,
    from_state TEXT CHECK (
        from_state IS NULL OR from_state IN (
            'unlabeled', 'predicted', 'reviewed', 'needs_adjudication',
            'adjudicated', 'gold_candidate', 'gold', 'retired'
        )
    ),
    to_state TEXT NOT NULL CHECK (
        to_state IN (
            'unlabeled', 'predicted', 'reviewed', 'needs_adjudication',
            'adjudicated', 'gold_candidate', 'gold', 'retired'
        )
    ),
    selected_revision_id UUID REFERENCES annotation_revisions (revision_id),
    actor_id UUID NOT NULL,
    transition_json JSONB NOT NULL CHECK (jsonb_typeof(transition_json) = 'object'),
    created_at TIMESTAMPTZ NOT NULL,
    FOREIGN KEY (dataset_id, case_id)
        REFERENCES golden_dataset_members (dataset_id, case_id) ON DELETE CASCADE
);

CREATE TABLE golden_dataset_snapshots (
    snapshot_id UUID PRIMARY KEY,
    dataset_id UUID NOT NULL,
    dataset_name TEXT NOT NULL CHECK (length(btrim(dataset_name)) BETWEEN 1 AND 200),
    dataset_version TEXT NOT NULL CHECK (length(btrim(dataset_version)) BETWEEN 1 AND 200),
    dataset_type TEXT NOT NULL CHECK (
        dataset_type IN (
            'development', 'frozen_test', 'challenge', 'shadow',
            'densitometry_reference', 'retrieval_evaluation'
        )
    ),
    predecessor_snapshot_id UUID REFERENCES golden_dataset_snapshots (snapshot_id),
    content_sha256 TEXT NOT NULL CHECK (content_sha256 ~ '^[a-f0-9]{64}$'),
    snapshot_json JSONB NOT NULL CHECK (jsonb_typeof(snapshot_json) = 'object'),
    frozen_by UUID NOT NULL,
    frozen_at TIMESTAMPTZ NOT NULL,
    UNIQUE (dataset_id),
    UNIQUE (dataset_name, dataset_version),
    UNIQUE (content_sha256)
);

ALTER TABLE golden_datasets
    ADD CONSTRAINT golden_dataset_predecessor_fk
    FOREIGN KEY (predecessor_snapshot_id)
    REFERENCES golden_dataset_snapshots (snapshot_id);

CREATE TABLE golden_dataset_exports (
    export_id UUID PRIMARY KEY,
    snapshot_id UUID NOT NULL REFERENCES golden_dataset_snapshots (snapshot_id),
    cases_jsonl_sha256 TEXT NOT NULL CHECK (cases_jsonl_sha256 ~ '^[a-f0-9]{64}$'),
    manifest_json_sha256 TEXT NOT NULL CHECK (manifest_json_sha256 ~ '^[a-f0-9]{64}$'),
    export_json JSONB NOT NULL CHECK (jsonb_typeof(export_json) = 'object'),
    created_at TIMESTAMPTZ NOT NULL,
    UNIQUE (snapshot_id)
);

CREATE INDEX golden_datasets_history_idx
    ON golden_datasets (dataset_name, created_at DESC, dataset_version, dataset_id);
CREATE INDEX golden_dataset_members_state_idx
    ON golden_dataset_members (dataset_id, case_state, split, case_id);
CREATE INDEX golden_dataset_member_artifacts_hash_idx
    ON golden_dataset_member_artifacts (dataset_id, sha256, split);
CREATE INDEX golden_case_transitions_history_idx
    ON golden_case_transitions (dataset_id, case_id, created_at, transition_id);
