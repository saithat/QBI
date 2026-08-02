"""Extraction-specific failures surfaced without losing pipeline history."""


class ExtractionError(RuntimeError):
    """Base error for a western-blot extraction operation."""


class UnsupportedExtractionSource(ExtractionError):
    """The stored artifact is not a supported PDF or image."""


class ExtractionImplementationNotFound(ExtractionError):
    """A request named an implementation that is not allowlisted."""


class ExtractionOutputInvalid(ExtractionError):
    """Raw model output could not enter the strict canonical result."""
