"""Canonical discovery ingestion and crawl-frontier state transitions."""

from __future__ import annotations

from collections.abc import Callable, Sequence
from datetime import UTC, datetime
from typing import Protocol
from uuid import UUID

from hiveblot_contracts import (
    ArtifactRecord,
    ArtifactReference,
    ArtifactVisibility,
    CrawlFrontierFilters,
    CrawlFrontierPage,
    CrawlFrontierRecord,
    DiscoveryBatch,
    DiscoveryEntityKind,
    DiscoveryIngestionResult,
)

from .errors import FrontierNotFound, InvalidDiscoveryRecord
from .normalization import normalize_source_url


class FrontierRepository(Protocol):
    def ingest_batch(self, batch: DiscoveryBatch) -> DiscoveryIngestionResult: ...

    def get(self, frontier_id: UUID) -> CrawlFrontierRecord | None: ...

    def list(
        self,
        filters: CrawlFrontierFilters,
        *,
        limit: int,
        offset: int,
    ) -> tuple[Sequence[CrawlFrontierRecord], int]: ...

    def update_schedule(
        self,
        frontier_id: UUID,
        *,
        expected_version: int,
        priority: int,
        next_eligible_fetch_at: datetime,
        updated_at: datetime,
    ) -> CrawlFrontierRecord: ...

    def retry(
        self,
        frontier_id: UUID,
        *,
        expected_version: int,
        next_eligible_fetch_at: datetime,
        rationale: str,
        updated_at: datetime,
    ) -> CrawlFrontierRecord: ...

    def record_acquisition(
        self,
        frontier_id: UUID,
        *,
        expected_version: int,
        artifact: ArtifactReference,
        trace_id: UUID,
        acquired_at: datetime,
    ) -> CrawlFrontierRecord: ...


class ArtifactLookup(Protocol):
    def get_artifact(self, artifact_id: UUID) -> ArtifactRecord: ...


class FrontierService:
    def __init__(
        self,
        repository: FrontierRepository,
        artifact_lookup: ArtifactLookup | None = None,
        *,
        clock: Callable[[], datetime] | None = None,
    ) -> None:
        self._repository = repository
        self._artifact_lookup = artifact_lookup
        self._clock = clock or (lambda: datetime.now(UTC))

    def ingest(self, batch: DiscoveryBatch) -> DiscoveryIngestionResult:
        normalized_records = tuple(
            record.model_copy(update={"canonical_url": normalize_source_url(record.canonical_url)})
            for record in batch.records
        )
        normalized = batch.model_copy(update={"records": normalized_records})
        entity_kinds_by_url: dict[str, set[DiscoveryEntityKind]] = {}
        for record in normalized.records:
            entity_kinds_by_url.setdefault(record.canonical_url, set()).add(record.entity_kind)
        if any(len(entity_kinds) > 1 for entity_kinds in entity_kinds_by_url.values()):
            raise InvalidDiscoveryRecord(
                "one canonical URL cannot represent different discovery entity kinds"
            )
        return self._repository.ingest_batch(normalized)

    def get(self, frontier_id: UUID) -> CrawlFrontierRecord:
        record = self._repository.get(frontier_id)
        if record is None:
            raise FrontierNotFound(f"frontier record {frontier_id} does not exist")
        return record

    def browse(
        self,
        filters: CrawlFrontierFilters,
        *,
        limit: int,
        offset: int,
    ) -> CrawlFrontierPage:
        if not 1 <= limit <= 200:
            raise ValueError("limit must be between 1 and 200")
        if offset < 0:
            raise ValueError("offset must be non-negative")
        records, total = self._repository.list(filters, limit=limit, offset=offset)
        next_offset = offset + len(records) if offset + len(records) < total else None
        return CrawlFrontierPage(
            filters=filters,
            items=tuple(records),
            total=total,
            limit=limit,
            offset=offset,
            next_offset=next_offset,
        )

    def update_schedule(
        self,
        frontier_id: UUID,
        *,
        expected_version: int,
        priority: int,
        next_eligible_fetch_at: datetime,
    ) -> CrawlFrontierRecord:
        return self._repository.update_schedule(
            frontier_id,
            expected_version=expected_version,
            priority=priority,
            next_eligible_fetch_at=next_eligible_fetch_at,
            updated_at=self._clock(),
        )

    def retry(
        self,
        frontier_id: UUID,
        *,
        expected_version: int,
        next_eligible_fetch_at: datetime,
        rationale: str,
    ) -> CrawlFrontierRecord:
        return self._repository.retry(
            frontier_id,
            expected_version=expected_version,
            next_eligible_fetch_at=next_eligible_fetch_at,
            rationale=rationale.strip(),
            updated_at=self._clock(),
        )

    def record_acquisition(
        self,
        frontier_id: UUID,
        *,
        expected_version: int,
        artifact_id: UUID,
        trace_id: UUID,
    ) -> CrawlFrontierRecord:
        if self._artifact_lookup is None:
            raise InvalidDiscoveryRecord("artifact lookup is unavailable")
        record = self._artifact_lookup.get_artifact(artifact_id)
        if record.visibility is not ArtifactVisibility.PUBLIC:
            raise InvalidDiscoveryRecord(
                "public discovery frontier records require public acquired artifacts"
            )
        artifact = ArtifactReference(
            artifact_id=record.artifact_id,
            sha256=record.sha256,
            media_type=record.media_type,
            byte_size=record.byte_size,
        )
        return self._repository.record_acquisition(
            frontier_id,
            expected_version=expected_version,
            artifact=artifact,
            trace_id=trace_id,
            acquired_at=self._clock(),
        )
