"""Evidence index and retrieval service failures."""


class EvidenceSearchError(Exception):
    """Base error for evidence indexing and retrieval."""


class EvidenceSearchNotFound(EvidenceSearchError):
    """Requested index, configuration, or evaluation record does not exist."""


class DuplicateEvidenceSearchRecord(EvidenceSearchError):
    """A stable index or dataset identity already exists."""


class InvalidEvidenceSearchState(EvidenceSearchError):
    """An index lifecycle or configuration invariant was violated."""


class EvidenceSearchSecurityInvariant(EvidenceSearchError):
    """Persistence returned a record outside the pre-authorized tenant scope."""
