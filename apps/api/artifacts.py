"""Immutable artifact ingestion and metadata API."""

from __future__ import annotations

from typing import Annotated, NoReturn
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Response, status
from hiveblot_auth import (
    AuthorizationService,
    AuthorizationTargetNotFound,
    InvalidAuthorizationState,
    PermissionDenied,
)
from hiveblot_contracts import (
    ArtifactRecord,
    ArtifactRelationship,
    ArtifactRelationshipKind,
    ArtifactVisibility,
    AuthenticatedPrincipal,
    AuthorizationPermission,
    ResourceScope,
)
from hiveblot_storage import (
    ArtifactNotFound,
    ArtifactService,
    ArtifactStorageError,
    CompletedPart,
    InvalidArtifact,
    InvalidUploadState,
    SourceAccessDenied,
)

from .artifact_dependencies import get_artifact_service
from .artifact_schemas import (
    ArtifactEventListResponse,
    ArtifactEventResponse,
    ArtifactPublicationResponse,
    ArtifactRelationshipInput,
    ArtifactRelationshipResponse,
    ArtifactResponse,
    BeginMultipartUploadRequest,
    BeginMultipartUploadResponse,
    CompleteMultipartUploadRequest,
    SignedArtifactUrlResponse,
    SourceIngestionRequest,
    UploadPartResponse,
)
from .auth_dependencies import (
    AuthorizationServiceDependency,
    PrincipalDependency,
    RequestIdDependency,
)

router = APIRouter(prefix="/api/v1", tags=["artifacts"])
ArtifactServiceDependency = Annotated[ArtifactService, Depends(get_artifact_service)]


@router.post(
    "/artifact-uploads",
    response_model=BeginMultipartUploadResponse,
    status_code=status.HTTP_201_CREATED,
)
def begin_multipart_upload(
    request: BeginMultipartUploadRequest,
    service: ArtifactServiceDependency,
    principal: PrincipalDependency,
    authorization: AuthorizationServiceDependency,
    request_id: RequestIdDependency,
) -> BeginMultipartUploadResponse:
    scope = _request_scope(request.visibility, request.organization_id)
    _authorize_scope(
        authorization,
        principal,
        AuthorizationPermission.ARTIFACT_WRITE,
        scope=scope,
        target_type="artifact_upload",
        request_id=request_id,
    )
    _authorize_relationships(
        request.relationships,
        service=service,
        authorization=authorization,
        principal=principal,
        request_id=request_id,
        target_scope=scope,
    )
    try:
        result = service.begin_multipart_upload(
            original_filename=request.original_filename,
            declared_media_type=request.declared_media_type,
            expected_byte_size=request.expected_byte_size,
            part_count=request.part_count,
            visibility=ArtifactVisibility(request.visibility),
            organization_id=request.organization_id,
            source_uri=request.source_uri,
            relationships=_relationships(request.relationships),
            actor_id=principal.user_id,
        )
    except ArtifactStorageError as exc:
        _raise_http(exc)
    return BeginMultipartUploadResponse(
        upload_id=result.upload_id,
        expires_at=result.expires_at,
        parts=tuple(
            UploadPartResponse(part_number=part.part_number, url=part.url) for part in result.parts
        ),
    )


@router.post(
    "/artifact-uploads/{upload_id}/complete",
    response_model=ArtifactPublicationResponse,
)
def complete_multipart_upload(
    upload_id: UUID,
    request: CompleteMultipartUploadRequest,
    service: ArtifactServiceDependency,
    principal: PrincipalDependency,
    authorization: AuthorizationServiceDependency,
    request_id: RequestIdDependency,
) -> ArtifactPublicationResponse:
    _authorize_resource(
        authorization,
        principal,
        AuthorizationPermission.ARTIFACT_WRITE,
        target_type="artifact_upload",
        target_id=upload_id,
        request_id=request_id,
    )
    try:
        result = service.complete_multipart_upload(
            upload_id,
            tuple(
                CompletedPart(part_number=part.part_number, etag=part.etag)
                for part in request.parts
            ),
            actor_id=principal.user_id,
        )
    except ArtifactStorageError as exc:
        _raise_http(exc)
    return ArtifactPublicationResponse(
        artifact=_artifact_response(result.artifact),
        deduplicated=result.deduplicated,
    )


@router.delete(
    "/artifact-uploads/{upload_id}",
    status_code=status.HTTP_204_NO_CONTENT,
)
def abort_multipart_upload(
    upload_id: UUID,
    service: ArtifactServiceDependency,
    principal: PrincipalDependency,
    authorization: AuthorizationServiceDependency,
    request_id: RequestIdDependency,
) -> Response:
    _authorize_resource(
        authorization,
        principal,
        AuthorizationPermission.ARTIFACT_WRITE,
        target_type="artifact_upload",
        target_id=upload_id,
        request_id=request_id,
    )
    try:
        service.abort_upload(upload_id, actor_id=principal.user_id)
    except ArtifactStorageError as exc:
        _raise_http(exc)
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.post(
    "/artifacts/source-ingestions",
    response_model=ArtifactPublicationResponse,
    status_code=status.HTTP_201_CREATED,
)
def ingest_source(
    request: SourceIngestionRequest,
    service: ArtifactServiceDependency,
    principal: PrincipalDependency,
    authorization: AuthorizationServiceDependency,
    request_id: RequestIdDependency,
) -> ArtifactPublicationResponse:
    scope = _request_scope(request.visibility, request.organization_id)
    _authorize_scope(
        authorization,
        principal,
        AuthorizationPermission.ARTIFACT_WRITE,
        scope=scope,
        target_type="artifact",
        request_id=request_id,
    )
    _authorize_relationships(
        request.relationships,
        service=service,
        authorization=authorization,
        principal=principal,
        request_id=request_id,
        target_scope=scope,
    )
    try:
        result = service.ingest_from_source(
            adapter_name=request.adapter_name,
            source_uri=request.source_uri,
            visibility=ArtifactVisibility(request.visibility),
            organization_id=request.organization_id,
            relationships=_relationships(request.relationships),
            actor_id=principal.user_id,
        )
    except ArtifactStorageError as exc:
        _raise_http(exc)
    return ArtifactPublicationResponse(
        artifact=_artifact_response(result.artifact),
        deduplicated=result.deduplicated,
    )


@router.get("/artifacts/{artifact_id}", response_model=ArtifactResponse)
def get_artifact(
    artifact_id: UUID,
    service: ArtifactServiceDependency,
    principal: PrincipalDependency,
    authorization: AuthorizationServiceDependency,
    request_id: RequestIdDependency,
) -> ArtifactResponse:
    try:
        record = service.get_artifact(artifact_id)
    except ArtifactStorageError as exc:
        _raise_http(exc)
    _authorize_artifact(
        record,
        authorization=authorization,
        principal=principal,
        request_id=request_id,
    )
    return _artifact_response(record)


@router.post(
    "/artifacts/{artifact_id}/download-url",
    response_model=SignedArtifactUrlResponse,
)
def create_download_url(
    artifact_id: UUID,
    service: ArtifactServiceDependency,
    principal: PrincipalDependency,
    authorization: AuthorizationServiceDependency,
    request_id: RequestIdDependency,
) -> SignedArtifactUrlResponse:
    try:
        record = service.get_artifact(artifact_id)
    except ArtifactStorageError as exc:
        _raise_http(exc)
    _authorize_artifact(
        record,
        authorization=authorization,
        principal=principal,
        request_id=request_id,
    )
    try:
        url, expires_at = service.create_download_url(
            artifact_id,
            actor_id=principal.user_id,
        )
    except ArtifactStorageError as exc:
        _raise_http(exc)
    return SignedArtifactUrlResponse(
        artifact_id=artifact_id,
        url=url,
        expires_at=expires_at,
    )


@router.get(
    "/artifacts/{artifact_id}/events",
    response_model=ArtifactEventListResponse,
)
def list_artifact_events(
    artifact_id: UUID,
    service: ArtifactServiceDependency,
    principal: PrincipalDependency,
    authorization: AuthorizationServiceDependency,
    request_id: RequestIdDependency,
) -> ArtifactEventListResponse:
    try:
        record = service.get_artifact(artifact_id)
    except ArtifactStorageError as exc:
        _raise_http(exc)
    _authorize_artifact(
        record,
        authorization=authorization,
        principal=principal,
        request_id=request_id,
    )
    try:
        events = service.list_events(artifact_id)
    except ArtifactStorageError as exc:
        _raise_http(exc)
    return ArtifactEventListResponse(
        artifact_id=artifact_id,
        events=tuple(
            ArtifactEventResponse(
                event_id=event.event_id,
                artifact_id=event.artifact_id,
                upload_id=event.upload_id,
                event_type=event.event_type.value,
                actor_id=event.actor_id,
                details=event.details,
                created_at=event.created_at,
            )
            for event in events
        ),
    )


def _request_scope(visibility: str, organization_id: UUID | None) -> ResourceScope:
    return ResourceScope(
        visibility=ArtifactVisibility(visibility),
        organization_id=organization_id,
    )


def _authorize_artifact(
    record: ArtifactRecord,
    *,
    authorization: AuthorizationService,
    principal: AuthenticatedPrincipal,
    request_id: UUID,
) -> None:
    _authorize_scope(
        authorization,
        principal,
        AuthorizationPermission.ARTIFACT_READ,
        scope=ResourceScope(
            visibility=record.visibility,
            organization_id=record.organization_id,
        ),
        target_type="artifact",
        target_id=record.artifact_id,
        request_id=request_id,
        conceal=True,
    )


def _authorize_relationships(
    relationships: tuple[ArtifactRelationshipInput, ...],
    *,
    service: ArtifactService,
    authorization: AuthorizationService,
    principal: AuthenticatedPrincipal,
    request_id: UUID,
    target_scope: ResourceScope,
) -> None:
    for relationship in relationships:
        try:
            related = service.get_artifact(relationship.related_artifact_id)
        except ArtifactStorageError as exc:
            _raise_http(exc)
        _authorize_artifact(
            related,
            authorization=authorization,
            principal=principal,
            request_id=request_id,
        )
        if target_scope.visibility is ArtifactVisibility.PUBLIC:
            if related.visibility is not ArtifactVisibility.PUBLIC:
                raise HTTPException(
                    status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
                    detail="public artifacts cannot reference private artifacts",
                )
        elif (
            related.visibility is ArtifactVisibility.ORGANIZATION_PRIVATE
            and related.organization_id != target_scope.organization_id
        ):
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
                detail="related private artifacts must belong to the target organization",
            )


def _authorize_resource(
    authorization: AuthorizationService,
    principal: AuthenticatedPrincipal,
    permission: AuthorizationPermission,
    *,
    target_type: str,
    target_id: UUID,
    request_id: UUID,
) -> None:
    try:
        authorization.authorize_resource(
            principal,
            permission,
            target_type=target_type,
            target_id=target_id,
            request_id=request_id,
        )
    except (AuthorizationTargetNotFound, PermissionDenied) as exc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Not found") from exc
    except InvalidAuthorizationState as exc:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(exc)) from exc


def _authorize_scope(
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
    except InvalidAuthorizationState as exc:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(exc)) from exc


def _relationships(
    values: tuple[ArtifactRelationshipInput, ...],
) -> tuple[ArtifactRelationship, ...]:
    return tuple(
        ArtifactRelationship(
            related_artifact_id=item.related_artifact_id,
            kind=ArtifactRelationshipKind(item.kind),
        )
        for item in values
    )


def _artifact_response(record: ArtifactRecord) -> ArtifactResponse:
    return ArtifactResponse(
        artifact_id=record.artifact_id,
        sha256=record.sha256,
        media_type=record.media_type,
        original_filename=record.original_filename,
        byte_size=record.byte_size,
        source_uri=record.source_uri,
        acquisition_method=record.acquisition_method,
        visibility=record.visibility,
        organization_id=record.organization_id,
        relationships=tuple(
            ArtifactRelationshipResponse(
                related_artifact_id=relationship.related_artifact_id,
                kind=relationship.kind,
            )
            for relationship in record.relationships
        ),
        created_at=record.created_at,
    )


def _raise_http(exc: ArtifactStorageError) -> NoReturn:
    if isinstance(exc, ArtifactNotFound):
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    if isinstance(exc, InvalidUploadState):
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    if isinstance(exc, SourceAccessDenied):
        raise HTTPException(status_code=403, detail=str(exc)) from exc
    if isinstance(exc, InvalidArtifact):
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    raise HTTPException(status_code=500, detail="artifact storage operation failed") from exc
