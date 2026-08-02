DROP TABLE evaluation_metric_case_results;
DROP TABLE evaluation_metric_runs;
DELETE FROM schema_migrations WHERE version = '0008_evaluation_metrics';
