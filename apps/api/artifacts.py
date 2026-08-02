"""Immutable artifact ingestion and metadata API."""

from __future__ import annotations

from typing import Annotated, NoReturn
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Response, status
from hiveblot_contracts import (
    ArtifactRecord,
    ArtifactRelationship,
    ArtifactRelationshipKind,
    ArtifactVisibility,
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
) -> BeginMultipartUploadResponse:
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
            actor_id=None,
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
) -> ArtifactPublicationResponse:
    try:
        result = service.complete_multipart_upload(
            upload_id,
            tuple(
                CompletedPart(part_number=part.part_number, etag=part.etag)
                for part in request.parts
            ),
            actor_id=None,
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
) -> Response:
    try:
        service.abort_upload(upload_id, actor_id=None)
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
) -> ArtifactPublicationResponse:
    try:
        result = service.ingest_from_source(
            adapter_name=request.adapter_name,
            source_uri=request.source_uri,
            visibility=ArtifactVisibility(request.visibility),
            organization_id=request.organization_id,
            relationships=_relationships(request.relationships),
            actor_id=None,
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
) -> ArtifactResponse:
    try:
        return _artifact_response(service.get_artifact(artifact_id))
    except ArtifactStorageError as exc:
        _raise_http(exc)


@router.post(
    "/artifacts/{artifact_id}/download-url",
    response_model=SignedArtifactUrlResponse,
)
def create_download_url(
    artifact_id: UUID,
    service: ArtifactServiceDependency,
) -> SignedArtifactUrlResponse:
    try:
        url, expires_at = service.create_download_url(artifact_id, actor_id=None)
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
) -> ArtifactEventListResponse:
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
