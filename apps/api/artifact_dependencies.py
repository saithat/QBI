"""Composition root for the artifact service."""

from functools import lru_cache

from hiveblot_storage import (
    ArtifactService,
    HttpSourceAdapter,
    PostgresArtifactRepository,
    S3ObjectStore,
    SourceAdapterRegistry,
)

from hiveblot.settings import get_settings


@lru_cache(maxsize=1)
def get_artifact_service() -> ArtifactService:
    settings = get_settings()
    object_store = S3ObjectStore(
        endpoint_url=settings.s3_endpoint_url,
        region=settings.s3_region,
        bucket=settings.s3_bucket,
        access_key_id=settings.s3_access_key_id.get_secret_value(),
        secret_access_key=settings.s3_secret_access_key.get_secret_value(),
    )
    object_store.ensure_bucket()
    adapters = SourceAdapterRegistry(
        {
            "http": HttpSourceAdapter(
                allowed_hosts=settings.source_ingest_allowed_hosts,
                max_bytes=settings.artifact_max_bytes,
            )
        }
    )
    return ArtifactService(
        repository=PostgresArtifactRepository(settings.database_url),
        object_store=object_store,
        source_adapters=adapters,
        max_bytes=settings.artifact_max_bytes,
        upload_url_seconds=settings.artifact_upload_url_seconds,
        download_url_seconds=settings.artifact_signed_url_seconds,
    )
