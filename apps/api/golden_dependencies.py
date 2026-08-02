"""Composition root for golden datasets and immutable S3 exports."""

from functools import lru_cache

from hiveblot_evaluation import (
    ContentAddressedDatasetExportStore,
    GoldenDatasetExportPublisher,
    GoldenDatasetService,
    PostgresGoldenDatasetRepository,
)
from hiveblot_storage import S3ObjectStore

from hiveblot.settings import get_settings

from .artifact_dependencies import get_artifact_service
from .evaluation_dependencies import get_evaluation_service


@lru_cache(maxsize=1)
def get_golden_dataset_service() -> GoldenDatasetService:
    settings = get_settings()
    object_store = S3ObjectStore(
        endpoint_url=settings.s3_endpoint_url,
        region=settings.s3_region,
        bucket=settings.s3_bucket,
        access_key_id=settings.s3_access_key_id.get_secret_value(),
        secret_access_key=settings.s3_secret_access_key.get_secret_value(),
    )
    object_store.ensure_bucket()
    exporter = GoldenDatasetExportPublisher(ContentAddressedDatasetExportStore(object_store))
    return GoldenDatasetService(
        PostgresGoldenDatasetRepository(settings.database_url),
        get_evaluation_service(),
        get_artifact_service(),
        exporter,
        signed_url_seconds=settings.artifact_signed_url_seconds,
    )
