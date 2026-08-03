"""Authentication and authorization composition for FastAPI routes."""

from __future__ import annotations

from functools import lru_cache
from typing import Annotated
from uuid import UUID, uuid4

from fastapi import Depends, HTTPException, Request, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from hiveblot_auth import (
    AuthorizationService,
    InMemoryAuthorizationRepository,
    InvalidCredentials,
    PostgresAuthorizationRepository,
)
from hiveblot_contracts import AuthenticatedPrincipal

from hiveblot.settings import AuthenticationMode, get_settings

_bearer = HTTPBearer(auto_error=False)


@lru_cache(maxsize=1)
def get_authorization_service() -> AuthorizationService:
    settings = get_settings()
    repository = (
        PostgresAuthorizationRepository(settings.database_url)
        if settings.authentication_mode is AuthenticationMode.BEARER
        else InMemoryAuthorizationRepository()
    )
    return AuthorizationService(
        repository,
        token_pepper=settings.auth_token_pepper.get_secret_value(),
    )


def get_request_id(request: Request) -> UUID:
    existing = getattr(request.state, "hiveblot_request_id", None)
    if isinstance(existing, UUID):
        return existing
    supplied = request.headers.get("X-Request-ID")
    if supplied is None:
        request_id = uuid4()
    else:
        try:
            request_id = UUID(supplied)
        except ValueError as exc:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="X-Request-ID must be a UUID",
            ) from exc
    request.state.hiveblot_request_id = request_id
    return request_id


def get_current_principal(
    request: Request,
    credentials: Annotated[HTTPAuthorizationCredentials | None, Depends(_bearer)],
    service: Annotated[AuthorizationService, Depends(get_authorization_service)],
) -> AuthenticatedPrincipal:
    existing = getattr(request.state, "hiveblot_principal", None)
    if isinstance(existing, AuthenticatedPrincipal):
        return existing
    settings = get_settings()
    if settings.authentication_mode is AuthenticationMode.DISABLED:
        principal = service.system_principal()
    else:
        if credentials is None or credentials.scheme.casefold() != "bearer":
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail="Bearer authentication is required",
                headers={"WWW-Authenticate": "Bearer"},
            )
        try:
            principal = service.authenticate(credentials.credentials)
        except InvalidCredentials as exc:
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail="Invalid bearer credential",
                headers={"WWW-Authenticate": "Bearer"},
            ) from exc
    request.state.hiveblot_principal = principal
    return principal


PrincipalDependency = Annotated[AuthenticatedPrincipal, Depends(get_current_principal)]
AuthorizationServiceDependency = Annotated[
    AuthorizationService,
    Depends(get_authorization_service),
]
RequestIdDependency = Annotated[UUID, Depends(get_request_id)]
