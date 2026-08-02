DROP TABLE IF EXISTS artifact_events;
DROP TABLE IF EXISTS artifact_uploads;
DROP TABLE IF EXISTS artifact_relationships;
DROP TABLE IF EXISTS artifacts;
DROP TABLE IF EXISTS artifact_blobs;
DELETE FROM schema_migrations WHERE version = '0002_artifact_storage';
