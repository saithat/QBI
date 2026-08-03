"""Consistent HTTP mapping for backend authorization decisions."""

from __future__ import annotations

from collections.abc import Callable, Iterable
from uuid import UUID

from fastapi import HTTPException, status
from hiveblot_auth import (
    AuthorizationError,
    AuthorizationService,
    AuthorizationTargetNotFound,
    InvalidAuthorizationState,
    PermissionDenied,
)
from hiveblot_contracts import (
    ArtifactVisibility,
    AuthenticatedPrincipal,
    AuthorizationPermission,
    ResourceScope,
)


def require_resource(
    authorization: AuthorizationService,
    principal: AuthenticatedPrincipal,
    permission: AuthorizationPermission,
    *,
    target_type: str,
    target_id: UUID,
    request_id: UUID,
    conceal: bool = True,
) -> ResourceScope:
    """Authorize an existing persisted resource and map denials consistently."""

    try:
        return authorization.authorize_resource(
            principal,
            permission,
            target_type=target_type,
            target_id=target_id,
            request_id=request_id,
        )
    except (AuthorizationTargetNotFound, PermissionDenied) as exc:
        if conceal:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="Not found",
            ) from exc
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Permission denied",
        ) from exc
    except InvalidAuthorizationState as exc:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=str(exc),
        ) from exc


def require_scope(
    authorization: AuthorizationService,
    principal: AuthenticatedPrincipal,
    permission: AuthorizationPermission,
    *,
    scope: ResourceScope,
    target_type: str,
    request_id: UUID,
    target_id: UUID | None = None,
    conceal: bool = False,
) -> None:
    """Authorize a requested scope before a new resource exists."""

    try:
        authorization.authorize_scope(
            principal,
            permission,
            scope=scope,
            target_type=target_type,
            target_id=target_id,
            request_id=request_id,
        )
    except PermissionDenied as exc:
        code = status.HTTP_404_NOT_FOUND if conceal else status.HTTP_403_FORBIDDEN
        detail = "Not found" if conceal else "Permission denied"
        raise HTTPException(status_code=code, detail=detail) from exc
    except AuthorizationError as exc:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=str(exc),
        ) from exc


def require_actor(
    principal: AuthenticatedPrincipal,
    actor_id: UUID,
    *,
    field_name: str,
) -> None:
    """Reject request bodies that try to attribute a mutation to another user."""

    if not principal.system and principal.user_id != actor_id:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail=f"{field_name} must match the authenticated user",
        )


def private_creation_scope(
    authorization: AuthorizationService,
    principal: AuthenticatedPrincipal,
    *,
    case_scope: ResourceScope,
    requested_visibility: ArtifactVisibility | None,
    requested_organization_id: UUID | None,
    permission: AuthorizationPermission,
    resource_name: str,
) -> ResourceScope:
    """Resolve a new resource to an explicit or private-by-default scope."""

    if requested_visibility is not None:
        return ResourceScope(
            visibility=requested_visibility,
            organization_id=requested_organization_id,
        )
    if case_scope.visibility is ArtifactVisibility.ORGANIZATION_PRIVATE:
        return case_scope
    organizations = authorization.organizations_with_permission(
        principal,
        permission,
    )
    if organizations is None:
        return ResourceScope(visibility=ArtifactVisibility.PUBLIC)
    if len(organizations) == 1:
        return ResourceScope(
            visibility=ArtifactVisibility.ORGANIZATION_PRIVATE,
            organization_id=organizations[0],
        )
    if not organizations:
        return ResourceScope(visibility=ArtifactVisibility.PUBLIC)
    raise HTTPException(
        status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
        detail=(
            f"visibility and organization_id are required for {resource_name} "
            "when the user belongs to multiple eligible organizations"
        ),
    )


def annotation_creation_scope(
    authorization: AuthorizationService,
    principal: AuthenticatedPrincipal,
    *,
    case_scope: ResourceScope,
    requested_visibility: ArtifactVisibility | None,
    requested_organization_id: UUID | None,
) -> ResourceScope:
    """Resolve new corrections to an explicit or private-by-default scope."""

    return private_creation_scope(
        authorization,
        principal,
        case_scope=case_scope,
        requested_visibility=requested_visibility,
        requested_organization_id=requested_organization_id,
        permission=AuthorizationPermission.ANNOTATION_WRITE,
        resource_name="annotations",
    )


def visible_records[ScopedRecord](
    values: Iterable[ScopedRecord],
    *,
    authorization: AuthorizationService,
    principal: AuthenticatedPrincipal,
    permission: AuthorizationPermission,
    scope_of: Callable[[ScopedRecord], ResourceScope],
) -> tuple[ScopedRecord, ...]:
    """Return only records allowed by the same policy used for direct access."""

    return tuple(
        value
        for value in values
        if authorization.is_allowed(principal, permission, scope=scope_of(value))
    )
