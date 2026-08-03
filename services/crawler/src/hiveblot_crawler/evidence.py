"""Immutable raw-response publication for discovery source adapters."""

from __future__ import annotations

import hashlib
from typing import Protocol

from hiveblot_contracts import ArtifactReference, ArtifactVisibility
from hiveblot_storage import ArtifactService


class DiscoveryEvidencePublisher(Protocol):
    def publish_response(
        self,
        *,
        request_url: str,
        media_type: str,
        content: bytes,
    ) -> ArtifactReference: ...


class ArtifactDiscoveryEvidencePublisher:
    """Publish exact source response bytes through immutable artifact storage."""

    def __init__(self, artifact_service: ArtifactService) -> None:
        self._artifact_service = artifact_service

    def publish_response(
        self,
        *,
        request_url: str,
        media_type: str,
        content: bytes,
    ) -> ArtifactReference:
        digest = hashlib.sha256(content).hexdigest()
        source_uri = (
            request_url
            if len(request_url) <= 2048
            else f"urn:hiveblot:discovery-response:sha256:{digest}"
        )
        record = self._artifact_service.publish_source_payload(
            original_filename=f"discovery-response-{digest}.xml",
            declared_media_type=media_type,
            content=content,
            source_uri=source_uri,
            visibility=ArtifactVisibility.PUBLIC,
            organization_id=None,
            relationships=(),
            actor_id=None,
        ).artifact
        return ArtifactReference(
            artifact_id=record.artifact_id,
            sha256=record.sha256,
            media_type=record.media_type,
            byte_size=record.byte_size,
        )
