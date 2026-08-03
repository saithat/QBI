"""API-first public discovery and crawl-frontier service."""

from .adapters import (
    PMC_OAI_BASE_URL,
    DiscoveryAdapter,
    PmcOaiDiscoveryAdapter,
)
from .errors import (
    DiscoveryError,
    DiscoverySourceError,
    DuplicateDiscoveryBatch,
    FrontierConcurrencyConflict,
    FrontierNotFound,
    InvalidDiscoveryRecord,
    InvalidFrontierState,
)
from .evidence import ArtifactDiscoveryEvidencePublisher, DiscoveryEvidencePublisher
from .normalization import canonical_pmc_accession, normalize_source_url, pmc_identity_key
from .postgres import PostgresFrontierRepository
from .service import ArtifactLookup, FrontierRepository, FrontierService

__all__ = [
    "PMC_OAI_BASE_URL",
    "ArtifactLookup",
    "ArtifactDiscoveryEvidencePublisher",
    "DiscoveryAdapter",
    "DiscoveryError",
    "DiscoveryEvidencePublisher",
    "DiscoverySourceError",
    "DuplicateDiscoveryBatch",
    "FrontierConcurrencyConflict",
    "FrontierNotFound",
    "FrontierRepository",
    "FrontierService",
    "InvalidDiscoveryRecord",
    "InvalidFrontierState",
    "PmcOaiDiscoveryAdapter",
    "PostgresFrontierRepository",
    "canonical_pmc_accession",
    "normalize_source_url",
    "pmc_identity_key",
]
