"""Public discovery and crawl-frontier errors."""


class DiscoveryError(RuntimeError):
    """Base error safe for service/API translation."""


class DiscoverySourceError(DiscoveryError):
    """An official source adapter returned unusable data."""


class InvalidDiscoveryRecord(DiscoveryError):
    """Canonical discovery data violates frontier invariants."""


class DuplicateDiscoveryBatch(DiscoveryError):
    """A reused batch identity contains different content."""


class FrontierNotFound(DiscoveryError):
    """A frontier or related record does not exist."""


class FrontierConcurrencyConflict(DiscoveryError):
    """A mutation used a stale frontier version."""


class InvalidFrontierState(DiscoveryError):
    """A requested frontier transition is not allowed."""
