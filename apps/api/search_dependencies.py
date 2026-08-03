"""Composition root for versioned evidence retrieval."""

from functools import lru_cache

from hiveblot_search import (
    DeterministicScientificEmbedder,
    EvidenceSearchService,
    PostgresEvidenceSearchRepository,
    TokenOverlapReranker,
)

from hiveblot.settings import get_settings


@lru_cache(maxsize=1)
def get_evidence_search_service() -> EvidenceSearchService:
    settings = get_settings()
    return EvidenceSearchService(
        PostgresEvidenceSearchRepository(settings.database_url),
        embedder=DeterministicScientificEmbedder(
            dimensions=settings.evidence_search_embedding_dimensions
        ),
        reranker=TokenOverlapReranker(),
        default_index_name=settings.evidence_search_index_name,
    )
