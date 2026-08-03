"""Provenance-aware, permission-safe evidence indexing and retrieval."""

from .embeddings import (
    DEFAULT_EMBEDDING_MODEL,
    DEFAULT_RERANKER,
    DeterministicScientificEmbedder,
    EmbeddingProvider,
    EvidenceReranker,
    TokenOverlapReranker,
    cosine_similarity,
)
from .errors import (
    DuplicateEvidenceSearchRecord,
    EvidenceSearchError,
    EvidenceSearchNotFound,
    EvidenceSearchSecurityInvariant,
    InvalidEvidenceSearchState,
)
from .evaluation import score_retrieval_results
from .memory import InMemoryEvidenceSearchRepository
from .postgres import PostgresEvidenceSearchRepository
from .repository import (
    EvidenceSearchRepository,
    SearchCandidate,
    SearchCandidatePage,
)
from .service import DEFAULT_RETRIEVAL_SCORER, EvidenceSearchService
from .text import (
    canonical_sha256,
    configuration_sha256,
    document_search_text,
    index_manifest_sha256,
    normalized_terms,
    retrieval_dataset_sha256,
)

__all__ = [
    "DEFAULT_EMBEDDING_MODEL",
    "DEFAULT_RERANKER",
    "DEFAULT_RETRIEVAL_SCORER",
    "DeterministicScientificEmbedder",
    "DuplicateEvidenceSearchRecord",
    "EmbeddingProvider",
    "EvidenceReranker",
    "EvidenceSearchError",
    "EvidenceSearchNotFound",
    "EvidenceSearchRepository",
    "EvidenceSearchSecurityInvariant",
    "EvidenceSearchService",
    "InMemoryEvidenceSearchRepository",
    "InvalidEvidenceSearchState",
    "PostgresEvidenceSearchRepository",
    "SearchCandidate",
    "SearchCandidatePage",
    "TokenOverlapReranker",
    "canonical_sha256",
    "configuration_sha256",
    "cosine_similarity",
    "document_search_text",
    "index_manifest_sha256",
    "normalized_terms",
    "retrieval_dataset_sha256",
    "score_retrieval_results",
]
