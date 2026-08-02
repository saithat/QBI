"""Composition root for deterministic densitometry."""

from functools import lru_cache

from hiveblot_densitometry import DensitometryGeometryResolver, DensitometryService
from hiveblot_evaluation import SpatialAnnotationService
from hiveblot_storage import PostgresArtifactRepository, S3ObjectStore, VerifiedArtifactReader

from hiveblot.settings import get_settings

from .artifact_dependencies import get_artifact_service
from .evaluation_dependencies import get_evaluation_service
from .pipeline_dependencies import get_pipeline_registry_service


@lru_cache(maxsize=1)
def get_densitometry_service() -> DensitometryService:
    settings = get_settings()
    object_store = S3ObjectStore(
        endpoint_url=settings.s3_endpoint_url,
        region=settings.s3_region,
        bucket=settings.s3_bucket,
        access_key_id=settings.s3_access_key_id.get_secret_value(),
        secret_access_key=settings.s3_secret_access_key.get_secret_value(),
    )
    object_store.ensure_bucket()
    artifact_service = get_artifact_service()
    reader = VerifiedArtifactReader(
        artifact_service,
        PostgresArtifactRepository(settings.database_url),
        object_store,
    )
    evaluation = get_evaluation_service()
    spatial = SpatialAnnotationService(evaluation)
    geometry = DensitometryGeometryResolver(evaluation, spatial, reader)
    return DensitometryService(
        reader,
        artifact_service,
        get_pipeline_registry_service(),
        geometry,
    )
