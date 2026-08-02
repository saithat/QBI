"""Verified read access to immutable artifacts used by extraction implementations."""

from __future__ import annotations

import hashlib
from typing import Protocol
from uuid import UUID

from hiveblot_contracts import ArtifactRecord
from hiveblot_storage import ArtifactService, ObjectStore

from .errors import ExtractionError


class ArtifactByteLocator(Protocol):
    def storage_key(self, artifact_id: UUID) -> str | None: ...


class ExtractionArtifactReader(Protocol):
    def get_artifact(self, artifact_id: UUID) -> ArtifactRecord: ...

    def read_bytes(self, artifact_id: UUID) -> bytes: ...


class VerifiedExtractionArtifactReader:
    def __init__(
        self,
        artifacts: ArtifactService,
        locator: ArtifactByteLocator,
        object_store: ObjectStore,
    ) -> None:
        self._artifacts = artifacts
        self._locator = locator
        self._object_store = object_store

    def get_artifact(self, artifact_id: UUID) -> ArtifactRecord:
        return self._artifacts.get_artifact(artifact_id)

    def read_bytes(self, artifact_id: UUID) -> bytes:
        artifact = self.get_artifact(artifact_id)
        key = self._locator.storage_key(artifact_id)
        if key is None:
            raise ExtractionError(f"artifact {artifact_id} has no immutable object")
        metadata = self._object_store.metadata(key)
        if (
            metadata is None
            or metadata.byte_size != artifact.byte_size
            or metadata.sha256 != artifact.sha256
        ):
            raise ExtractionError("stored source metadata does not match its artifact record")
        content = b"".join(self._object_store.iter_bytes(key))
        if len(content) != artifact.byte_size:
            raise ExtractionError("stored source size changed while it was being read")
        if hashlib.sha256(content).hexdigest() != artifact.sha256:
            raise ExtractionError("stored source bytes do not match their SHA-256")
        return content
