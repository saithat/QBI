"""Deterministic golden-dataset export rendering and object publication."""

from __future__ import annotations

import hashlib
import io
import json
from collections import Counter
from typing import Any, BinaryIO, Protocol
from uuid import NAMESPACE_URL, uuid4, uuid5

from hiveblot_contracts import (
    GoldenDatasetArtifactManifestEntry,
    GoldenDatasetCaseExport,
    GoldenDatasetExportManifest,
    GoldenDatasetExportObject,
    GoldenDatasetExportRecord,
    GoldenDatasetSnapshot,
    GoldenDatasetSplitSummary,
)


class DatasetExportStore(Protocol):
    def publish(
        self,
        *,
        filename: str,
        media_type: str,
        content: bytes,
    ) -> GoldenDatasetExportObject: ...

    def download_url(self, value: GoldenDatasetExportObject, *, expires_in: int) -> str: ...


class ImmutableObjectStore(Protocol):
    def stage_file(self, key: str, stream: BinaryIO) -> None: ...

    def publish(
        self,
        staging_key: str,
        canonical_key: str,
        *,
        media_type: str,
        sha256: str,
        byte_size: int,
    ) -> None: ...

    def delete(self, key: str) -> None: ...

    def metadata(self, key: str) -> Any | None: ...

    def presign_download(self, key: str, expires_in: int) -> str: ...


class ContentAddressedDatasetExportStore:
    """Publish generated exports under immutable SHA-256 object keys."""

    def __init__(self, object_store: ImmutableObjectStore) -> None:
        self._object_store = object_store

    def publish(
        self,
        *,
        filename: str,
        media_type: str,
        content: bytes,
    ) -> GoldenDatasetExportObject:
        sha256 = hashlib.sha256(content).hexdigest()
        canonical_key = f"dataset-exports/sha256/{sha256[:2]}/{sha256[2:4]}/{sha256}/{filename}"
        metadata = self._object_store.metadata(canonical_key)
        if metadata is None:
            staging_key = f"staging/dataset-exports/{uuid4()}"
            try:
                self._object_store.stage_file(staging_key, io.BytesIO(content))
                self._object_store.publish(
                    staging_key,
                    canonical_key,
                    media_type=media_type,
                    sha256=sha256,
                    byte_size=len(content),
                )
            finally:
                if self._object_store.metadata(staging_key) is not None:
                    self._object_store.delete(staging_key)
        else:
            if (metadata.byte_size, metadata.sha256, metadata.media_type) != (
                len(content),
                sha256,
                media_type,
            ):
                raise ValueError("dataset export object conflicts with its content hash")
        return GoldenDatasetExportObject(
            filename=filename,
            media_type=media_type,
            sha256=sha256,
            byte_size=len(content),
            storage_key=canonical_key,
        )

    def download_url(self, value: GoldenDatasetExportObject, *, expires_in: int) -> str:
        return self._object_store.presign_download(value.storage_key, expires_in)


class GoldenDatasetExportPublisher:
    def __init__(self, store: DatasetExportStore) -> None:
        self._store = store

    def publish(self, snapshot: GoldenDatasetSnapshot) -> GoldenDatasetExportRecord:
        cases_jsonl_bytes = render_cases_jsonl(snapshot)
        cases_object = self._store.publish(
            filename="cases.jsonl",
            media_type="application/x-ndjson",
            content=cases_jsonl_bytes,
        )
        manifest = build_export_manifest(snapshot, cases_object)
        manifest_bytes = render_export_manifest(manifest)
        manifest_object = self._store.publish(
            filename="manifest.json",
            media_type="application/json",
            content=manifest_bytes,
        )
        export_id = uuid5(
            NAMESPACE_URL,
            f"urn:hiveblot:golden-export:{snapshot.snapshot_id}:"
            f"{cases_object.sha256}:{manifest_object.sha256}",
        )
        return GoldenDatasetExportRecord(
            export_id=export_id,
            snapshot_id=snapshot.snapshot_id,
            cases_jsonl=cases_object,
            manifest_json=manifest_object,
            created_at=snapshot.frozen_at,
        )

    def download_url(
        self,
        value: GoldenDatasetExportObject,
        *,
        expires_in: int,
    ) -> str:
        return self._store.download_url(value, expires_in=expires_in)


def render_cases_jsonl(snapshot: GoldenDatasetSnapshot) -> bytes:
    lines = []
    for case in sorted(snapshot.cases, key=lambda item: str(item.case_id)):
        record = GoldenDatasetCaseExport(
            snapshot_id=snapshot.snapshot_id,
            dataset_name=snapshot.dataset_name,
            dataset_version=snapshot.dataset_version,
            dataset_type=snapshot.dataset_type,
            case=case,
        )
        rendered = json.dumps(
            record.model_dump(mode="json"),
            sort_keys=True,
            separators=(",", ":"),
            ensure_ascii=False,
        )
        GoldenDatasetCaseExport.model_validate_json(rendered)
        lines.append(rendered)
    return ("\n".join(lines) + "\n").encode()


def build_export_manifest(
    snapshot: GoldenDatasetSnapshot,
    cases_jsonl: GoldenDatasetExportObject,
) -> GoldenDatasetExportManifest:
    case_counts = Counter(item.split for item in snapshot.cases)
    paper_counts = {
        split: len({item.paper_key for item in snapshot.cases if item.split is split})
        for split in case_counts
    }
    artifacts = {
        source.artifact.artifact_id: GoldenDatasetArtifactManifestEntry(
            artifact_id=source.artifact.artifact_id,
            sha256=source.artifact.sha256,
            media_type=source.artifact.media_type,
            byte_size=source.artifact.byte_size,
        )
        for case in snapshot.cases
        for source in case.source_artifacts
    }
    return GoldenDatasetExportManifest(
        snapshot_id=snapshot.snapshot_id,
        dataset_name=snapshot.dataset_name,
        dataset_version=snapshot.dataset_version,
        dataset_type=snapshot.dataset_type,
        dataset_sha256=snapshot.content_sha256,
        frozen_at=snapshot.frozen_at,
        case_count=len(snapshot.cases),
        splits=tuple(
            GoldenDatasetSplitSummary(
                split=split,
                case_count=case_counts[split],
                paper_count=paper_counts[split],
            )
            for split in sorted(case_counts, key=lambda value: value.value)
        ),
        artifacts=tuple(artifacts[key] for key in sorted(artifacts, key=str)),
        cases_jsonl=cases_jsonl,
        changelog=snapshot.changelog,
    )


def render_export_manifest(manifest: GoldenDatasetExportManifest) -> bytes:
    rendered = (
        json.dumps(
            manifest.model_dump(mode="json"),
            sort_keys=True,
            separators=(",", ":"),
            ensure_ascii=False,
        )
        + "\n"
    )
    GoldenDatasetExportManifest.model_validate_json(rendered)
    return rendered.encode()
