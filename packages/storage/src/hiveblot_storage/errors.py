"""Stable service errors translated by application adapters."""


class ArtifactStorageError(Exception):
    """Base class for artifact service failures."""


class ArtifactNotFound(ArtifactStorageError):
    """The requested artifact or upload session does not exist."""


class InvalidUploadState(ArtifactStorageError):
    """The requested upload transition is not valid."""


class InvalidArtifact(ArtifactStorageError):
    """Staged bytes are unsupported or inconsistent with their metadata."""


class SourceAccessDenied(ArtifactStorageError):
    """A source adapter is not allowed to access the requested location."""
