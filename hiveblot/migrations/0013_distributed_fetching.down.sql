DROP TABLE crawl_downstream_tasks;
DROP TABLE crawl_robots_cache;
DROP TABLE crawl_fetch_attempts;
DROP TABLE crawl_fetch_tasks;
DROP TABLE crawl_domain_permits;
DROP TABLE crawl_domain_policies;
ALTER TABLE crawl_frontier
    DROP COLUMN last_http_status,
    DROP COLUMN last_fetch_at,
    DROP COLUMN last_modified_header,
    DROP COLUMN etag;
DELETE FROM schema_migrations WHERE version = '0013_distributed_fetching';
