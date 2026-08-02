"""Densitometry domain errors."""


class DensitometryError(Exception):
    """Base deterministic densitometry error."""


class InvalidDensitometryInput(DensitometryError):
    """Raised when source bytes or geometry cannot be measured safely."""


class DensitometryRunNotFound(DensitometryError):
    """Raised when a requested pipeline run is not a densitometry run."""
