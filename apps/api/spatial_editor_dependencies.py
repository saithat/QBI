"""Composition root for spatial annotation editing."""

from functools import lru_cache

from hiveblot_evaluation import SpatialAnnotationService

from .evaluation_dependencies import get_evaluation_service


@lru_cache(maxsize=1)
def get_spatial_annotation_service() -> SpatialAnnotationService:
    return SpatialAnnotationService(get_evaluation_service())
