"""S3-compatible object-store boundary and implementation."""

from __future__ import annotations

from collections.abc import Iterator, Sequence
from dataclasses import dataclass
from typing import Any, BinaryIO, Protocol

import boto3
from botocore.client import Config
from botocore.exceptions import ClientError

from .models import CompletedPart


@dataclass(frozen=True, slots=True)
class StoredObjectMetadata:
    byte_size: int
    media_type: str | None
    sha256: str | None


class ObjectStore(Protocol):
    """Domain-independent operations needed for immutable publication."""

    def ensure_bucket(self) -> None: ...

    def create_multipart(self, key: str) -> str: ...

    def presign_upload_part(
        self,
        key: str,
        backend_upload_id: str,
        part_number: int,
        expires_in: int,
    ) -> str: ...

    def complete_multipart(
        self,
        key: str,
        backend_upload_id: str,
        parts: Sequence[CompletedPart],
    ) -> None: ...

    def abort_multipart(self, key: str, backend_upload_id: str) -> None: ...

    def stage_file(self, key: str, stream: BinaryIO) -> None: ...

    def iter_bytes(self, key: str, chunk_size: int = 1024 * 1024) -> Iterator[bytes]: ...

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

    def metadata(self, key: str) -> StoredObjectMetadata | None: ...

    def presign_download(self, key: str, expires_in: int) -> str: ...


class S3ObjectStore:
    """S3/MinIO adapter; only staging keys may be mutable."""

    def __init__(
        self,
        *,
        endpoint_url: str,
        region: str,
        bucket: str,
        access_key_id: str,
        secret_access_key: str,
        client: Any | None = None,
    ) -> None:
        self._bucket = bucket
        self._client = client or boto3.client(
            "s3",
            endpoint_url=endpoint_url,
            region_name=region,
            aws_access_key_id=access_key_id,
            aws_secret_access_key=secret_access_key,
            config=Config(signature_version="s3v4", s3={"addressing_style": "path"}),
        )

    def ensure_bucket(self) -> None:
        try:
            self._client.head_bucket(Bucket=self._bucket)
        except ClientError as exc:
            code = str(exc.response.get("Error", {}).get("Code", ""))
            if code not in {"404", "NoSuchBucket", "NotFound"}:
                raise
            self._client.create_bucket(Bucket=self._bucket)

    def create_multipart(self, key: str) -> str:
        response = self._client.create_multipart_upload(
            Bucket=self._bucket,
            Key=key,
            ContentType="application/octet-stream",
        )
        return str(response["UploadId"])

    def presign_upload_part(
        self,
        key: str,
        backend_upload_id: str,
        part_number: int,
        expires_in: int,
    ) -> str:
        return str(
            self._client.generate_presigned_url(
                "upload_part",
                Params={
                    "Bucket": self._bucket,
                    "Key": key,
                    "UploadId": backend_upload_id,
                    "PartNumber": part_number,
                },
                ExpiresIn=expires_in,
            )
        )

    def complete_multipart(
        self,
        key: str,
        backend_upload_id: str,
        parts: Sequence[CompletedPart],
    ) -> None:
        self._client.complete_multipart_upload(
            Bucket=self._bucket,
            Key=key,
            UploadId=backend_upload_id,
            MultipartUpload={
                "Parts": [
                    {"PartNumber": part.part_number, "ETag": part.etag}
                    for part in sorted(parts, key=lambda item: item.part_number)
                ]
            },
        )

    def abort_multipart(self, key: str, backend_upload_id: str) -> None:
        self._client.abort_multipart_upload(
            Bucket=self._bucket,
            Key=key,
            UploadId=backend_upload_id,
        )

    def stage_file(self, key: str, stream: BinaryIO) -> None:
        stream.seek(0)
        self._client.upload_fileobj(
            stream,
            self._bucket,
            key,
            ExtraArgs={"ContentType": "application/octet-stream"},
        )

    def iter_bytes(self, key: str, chunk_size: int = 1024 * 1024) -> Iterator[bytes]:
        response = self._client.get_object(Bucket=self._bucket, Key=key)
        body = response["Body"]
        try:
            while chunk := body.read(chunk_size):
                yield bytes(chunk)
        finally:
            body.close()

    def publish(
        self,
        staging_key: str,
        canonical_key: str,
        *,
        media_type: str,
        sha256: str,
        byte_size: int,
    ) -> None:
        existing = self.metadata(canonical_key)
        if existing is not None:
            if existing.byte_size != byte_size or existing.sha256 != sha256:
                raise ValueError("canonical object metadata conflicts with its content hash")
            self.delete(staging_key)
            return

        self._client.copy_object(
            Bucket=self._bucket,
            Key=canonical_key,
            CopySource={"Bucket": self._bucket, "Key": staging_key},
            ContentType=media_type,
            Metadata={"sha256": sha256},
            MetadataDirective="REPLACE",
        )
        published = self.metadata(canonical_key)
        if published is None or published.byte_size != byte_size:
            raise ValueError("published object failed size verification")
        self.delete(staging_key)

    def delete(self, key: str) -> None:
        self._client.delete_object(Bucket=self._bucket, Key=key)

    def metadata(self, key: str) -> StoredObjectMetadata | None:
        try:
            response = self._client.head_object(Bucket=self._bucket, Key=key)
        except ClientError as exc:
            code = str(exc.response.get("Error", {}).get("Code", ""))
            if code in {"404", "NoSuchKey", "NotFound"}:
                return None
            raise
        metadata = response.get("Metadata", {})
        return StoredObjectMetadata(
            byte_size=int(response["ContentLength"]),
            media_type=response.get("ContentType"),
            sha256=metadata.get("sha256"),
        )

    def presign_download(self, key: str, expires_in: int) -> str:
        return str(
            self._client.generate_presigned_url(
                "get_object",
                Params={"Bucket": self._bucket, "Key": key},
                ExpiresIn=expires_in,
            )
        )
