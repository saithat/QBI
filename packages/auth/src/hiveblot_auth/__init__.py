"""Organization-scoped backend authorization for HiveBlot."""

from .errors import (
    AuthorizationError,
    AuthorizationTargetNotFound,
    InvalidAuthorizationState,
    InvalidCredentials,
    PermissionDenied,
)
from .memory import InMemoryAuthorizationRepository
from .postgres import PostgresAuthorizationRepository
from .repository import AuthorizationRepository
from .service import SYSTEM_USER_ID, AuthorizationService, token_digest

__all__ = [
    "AuthorizationError",
    "AuthorizationRepository",
    "AuthorizationService",
    "AuthorizationTargetNotFound",
    "InvalidAuthorizationState",
    "InvalidCredentials",
    "InMemoryAuthorizationRepository",
    "PermissionDenied",
    "PostgresAuthorizationRepository",
    "SYSTEM_USER_ID",
    "token_digest",
]
