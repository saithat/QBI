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
    FetchLeaseLost,
    FetchTaskNotFound,
    FetchValidationError,
    FrontierConcurrencyConflict,
    FrontierNotFound,
    InvalidDiscoveryRecord,
    InvalidFrontierState,
)
from .evidence import ArtifactDiscoveryEvidencePublisher, DiscoveryEvidencePublisher
from .fetch_postgres import PostgresFetchRepository
from .fetch_service import (
    FetchClient,
    FetchedPayload,
    FetchFailure,
    FetchQueueRepository,
    FetchQueueService,
    FetchWorker,
    SourcePayloadPublisher,
)
from .http_fetcher import HttpFetchClient, RobotsCache, RobotsCacheEntry, parse_retry_after
from .normalization import canonical_pmc_accession, normalize_source_url, pmc_identity_key
from .postgres import PostgresFrontierRepository
from .rate_limit import (
    DomainPermitRepository,
    DomainRateLimitTimeout,
    SharedDomainRateLimiter,
)
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
    "DomainPermitRepository",
    "DomainRateLimitTimeout",
    "FetchClient",
    "FetchFailure",
    "FetchLeaseLost",
    "FetchQueueRepository",
    "FetchQueueService",
    "FetchTaskNotFound",
    "FetchValidationError",
    "FetchWorker",
    "FetchedPayload",
    "FrontierConcurrencyConflict",
    "FrontierNotFound",
    "FrontierRepository",
    "FrontierService",
    "InvalidDiscoveryRecord",
    "InvalidFrontierState",
    "HttpFetchClient",
    "PmcOaiDiscoveryAdapter",
    "PostgresFrontierRepository",
    "PostgresFetchRepository",
    "RobotsCache",
    "RobotsCacheEntry",
    "SharedDomainRateLimiter",
    "SourcePayloadPublisher",
    "canonical_pmc_accession",
    "normalize_source_url",
    "pmc_identity_key",
    "parse_retry_after",
]
