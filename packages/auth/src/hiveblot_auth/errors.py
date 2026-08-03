"""Authorization-domain failures without HTTP coupling."""


class AuthorizationError(Exception):
    """Base authorization failure."""


class InvalidCredentials(AuthorizationError):
    """A bearer credential is missing, malformed, expired, revoked, or unknown."""


class PermissionDenied(AuthorizationError):
    """An authenticated principal lacks a required permission."""


class AuthorizationTargetNotFound(AuthorizationError):
    """A scoped resource cannot be resolved."""


class InvalidAuthorizationState(AuthorizationError):
    """Identity or organization state violates an authorization invariant."""
