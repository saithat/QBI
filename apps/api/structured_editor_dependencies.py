"""Composition root for the typed western-blot annotation editor."""

from functools import lru_cache

from hiveblot_evaluation import StructuredAnnotationService

from .evaluation_dependencies import get_evaluation_service


@lru_cache(maxsize=1)
def get_structured_annotation_service() -> StructuredAnnotationService:
    return StructuredAnnotationService(get_evaluation_service())
