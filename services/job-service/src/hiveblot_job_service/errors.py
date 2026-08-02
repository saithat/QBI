"""Job-service failures with stable API semantics."""


class JobServiceError(RuntimeError):
    """Base class for generic job-service failures."""


class JobNotFound(JobServiceError):
    """Requested job, attempt, or lease does not exist."""


class DuplicateJob(JobServiceError):
    """An identifier or idempotency key conflicts with different content."""


class InvalidJobState(JobServiceError):
    """Requested transition is not valid for the current durable state."""


class LeaseLost(JobServiceError):
    """A worker no longer owns a valid lease."""


class ExecutorError(JobServiceError):
    """A container could not be prepared or executed."""

    def __init__(self, message: str, *, retryable: bool) -> None:
        super().__init__(message)
        self.retryable = retryable


class InvalidExecutionOutput(ExecutorError):
    def __init__(self, message: str) -> None:
        super().__init__(message, retryable=False)
