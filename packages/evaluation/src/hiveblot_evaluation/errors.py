"""Stable evaluation service errors translated by application adapters."""


class EvaluationError(Exception):
    """Base class for evaluation persistence and validation failures."""


class EvaluationNotFound(EvaluationError):
    """A requested case, prediction, annotation, or revision does not exist."""


class ConcurrencyConflict(EvaluationError):
    """An optimistic concurrency token no longer matches current state."""


class DuplicateEvaluationRecord(EvaluationError):
    """A stable identity already exists where a new record was requested."""


class InvalidEvaluationState(EvaluationError):
    """The requested relationship or state transition violates review invariants."""
