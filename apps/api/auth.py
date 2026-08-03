"""Authenticated principal, organization membership, and audit APIs."""

from __future__ import annotations

from typing import Annotated, NoReturn
from uuid import UUID

from fastapi import APIRouter, HTTPException, Query, status
from hiveblot_auth import (
    AuthorizationError,
    InvalidAuthorizationState,
    PermissionDenied,
)
from hiveblot_contracts import OrganizationRole

from .auth_dependencies import (
    AuthorizationServiceDependency,
    PrincipalDependency,
    RequestIdDependency,
)
from .auth_schemas import (
    AuditEventListResponse,
    AuditEventResponse,
    CreateOrganizationRequest,
    CurrentPrincipalResponse,
    MembershipResponse,
    OrganizationResponse,
    SetMembershipRequest,
)

router = APIRouter(prefix="/api/v1", tags=["authorization"])


@router.get("/auth/me", response_model=CurrentPrincipalResponse)
def current_principal(principal: PrincipalDependency) -> CurrentPrincipalResponse:
    return CurrentPrincipalResponse(
        user_id=principal.user_id,
        token_id=principal.token_id,
        system=principal.system,
        platform_operator=principal.platform_operator,
        memberships=tuple(
            MembershipResponse.model_validate(value.model_dump(mode="python"))
            for value in principal.memberships
        ),
    )


@router.post(
    "/organizations",
    response_model=OrganizationResponse,
    status_code=status.HTTP_201_CREATED,
)
def create_organization(
    request: CreateOrganizationRequest,
    principal: PrincipalDependency,
    service: AuthorizationServiceDependency,
    request_id: RequestIdDependency,
) -> OrganizationResponse:
    try:
        value = service.create_organization(
            principal,
            slug=request.slug,
            display_name=request.display_name,
            request_id=request_id,
        )
    except AuthorizationError as exc:
        _raise_http(exc)
    return OrganizationResponse.model_validate(value.model_dump(mode="python"))


@router.put(
    "/organizations/{organization_id}/members/{user_id}",
    response_model=MembershipResponse,
)
def set_membership(
    organization_id: UUID,
    user_id: UUID,
    request: SetMembershipRequest,
    principal: PrincipalDependency,
    service: AuthorizationServiceDependency,
    request_id: RequestIdDependency,
) -> MembershipResponse:
    try:
        value = service.set_membership(
            principal,
            organization_id=organization_id,
            user_id=user_id,
            role=OrganizationRole(request.role),
            active=request.active,
            request_id=request_id,
        )
    except AuthorizationError as exc:
        _raise_http(exc)
    return MembershipResponse.model_validate(value.model_dump(mode="python"))


@router.get(
    "/organizations/{organization_id}/audit-events",
    response_model=AuditEventListResponse,
)
def list_audit_events(
    organization_id: UUID,
    principal: PrincipalDependency,
    service: AuthorizationServiceDependency,
    request_id: RequestIdDependency,
    limit: Annotated[int, Query(ge=1, le=500)] = 100,
) -> AuditEventListResponse:
    try:
        values = service.list_audit_events(
            principal,
            organization_id=organization_id,
            request_id=request_id,
            limit=limit,
        )
    except AuthorizationError as exc:
        _raise_http(exc)
    return AuditEventListResponse(
        organization_id=organization_id,
        events=tuple(
            AuditEventResponse.model_validate(value.model_dump(mode="python")) for value in values
        ),
    )


def _raise_http(exc: AuthorizationError) -> NoReturn:
    if isinstance(exc, PermissionDenied):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN, detail="Permission denied"
        ) from exc
    if isinstance(exc, InvalidAuthorizationState):
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=str(exc),
        ) from exc
    raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_CONTENT, detail=str(exc)) from exc
