DROP TRIGGER organization_membership_platform_operator_guard ON organization_memberships;
DROP FUNCTION hiveblot_protect_platform_operator_membership();

DROP TRIGGER retrieval_evaluation_run_integrity ON retrieval_evaluation_runs;
DROP FUNCTION hiveblot_enforce_retrieval_evaluation_run();
DROP TRIGGER retrieval_evaluation_dataset_integrity ON retrieval_evaluation_datasets;
DROP FUNCTION hiveblot_enforce_retrieval_dataset_json();
DROP TRIGGER retrieval_evaluation_runs_immutable ON retrieval_evaluation_runs;
DROP TRIGGER retrieval_evaluation_datasets_immutable ON retrieval_evaluation_datasets;
DROP FUNCTION hiveblot_prevent_retrieval_snapshot_mutation();
DROP TRIGGER evidence_index_document_citation_scope ON evidence_index_document_citations;
DROP FUNCTION hiveblot_enforce_index_citation_scope();
DROP TRIGGER evidence_index_citation_immutable ON evidence_index_document_citations;
DROP TRIGGER evidence_index_document_immutable ON evidence_index_documents;
DROP FUNCTION hiveblot_prevent_completed_index_document_mutation();
DROP TRIGGER evidence_index_document_integrity ON evidence_index_documents;
DROP FUNCTION hiveblot_enforce_index_document();
DROP TRIGGER evidence_index_version_lifecycle ON evidence_index_versions;
DROP FUNCTION hiveblot_enforce_index_version_lifecycle();
DROP TRIGGER evidence_index_version_configuration ON evidence_index_versions;
DROP FUNCTION hiveblot_enforce_index_version_configuration();
DROP TRIGGER evidence_index_configuration_json ON evidence_index_configurations;
DROP FUNCTION hiveblot_enforce_index_configuration_json();
DROP TRIGGER evidence_index_configurations_immutable ON evidence_index_configurations;
DROP FUNCTION hiveblot_enforce_index_configuration_immutable();

ALTER TABLE evidence_index_versions
    DROP CONSTRAINT evidence_index_versions_activation_evaluation_fk;

DROP TABLE retrieval_evaluation_runs;
DROP TABLE retrieval_evaluation_datasets;
DROP TABLE evidence_index_document_citations;
DROP TABLE evidence_index_documents;
DROP TABLE evidence_index_versions;
DROP TABLE evidence_index_configurations;

DELETE FROM auth_users
WHERE user_id = '00000000-0000-0000-0000-000000000001'
  AND NOT EXISTS (
      SELECT 1 FROM authorization_audit_events
      WHERE actor_user_id = '00000000-0000-0000-0000-000000000001'
  )
  AND NOT EXISTS (
      SELECT 1 FROM organization_memberships
      WHERE user_id = '00000000-0000-0000-0000-000000000001'
  )
  AND NOT EXISTS (
      SELECT 1 FROM api_access_tokens
      WHERE user_id = '00000000-0000-0000-0000-000000000001'
  );
