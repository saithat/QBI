DROP TABLE crawl_frontier_events;
DROP TABLE crawl_frontier_artifacts;
DROP TABLE crawl_frontier_relationships;
DROP TABLE discovery_observations;
DROP TABLE crawl_frontier_aliases;
DROP TABLE crawl_frontier;
DROP TABLE discovery_run_evidence;
DROP TABLE discovery_runs;
DELETE FROM schema_migrations WHERE version = '0012_public_discovery_frontier';
