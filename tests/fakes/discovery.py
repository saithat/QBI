from __future__ import annotations

import hashlib
from collections.abc import Sequence
from datetime import datetime
from uuid import UUID, uuid4

from hiveblot_contracts import (
    ArtifactReference,
    CrawlFrontierFilters,
    CrawlFrontierRecord,
    CrawlFrontierRelationship,
    CrawlFrontierStatus,
    DiscoveryAccessStatus,
    DiscoveryArtifactAcquisition,
    DiscoveryBatch,
    DiscoveryIngestionResult,
    DiscoveryRecord,
    ToolIdentifier,
    discovery_batch_sha256,
)
from hiveblot_crawler import (
    DuplicateDiscoveryBatch,
    FrontierConcurrencyConflict,
    FrontierNotFound,
    InvalidDiscoveryRecord,
    InvalidFrontierState,
)


class InMemoryDiscoveryEvidencePublisher:
    def __init__(self) -> None:
        self.responses: list[tuple[str, str, bytes, ArtifactReference]] = []

    def publish_response(
        self,
        *,
        request_url: str,
        media_type: str,
        content: bytes,
    ) -> ArtifactReference:
        reference = ArtifactReference(
            artifact_id=uuid4(),
            sha256=hashlib.sha256(content).hexdigest(),
            media_type=media_type,
            byte_size=len(content),
        )
        self.responses.append((request_url, media_type, content, reference))
        return reference


class InMemoryFrontierRepository:
    def __init__(self) -> None:
        self.records: dict[UUID, CrawlFrontierRecord] = {}
        self.identities: dict[str, UUID] = {}
        self.urls: dict[str, UUID] = {}
        self.batches: dict[UUID, tuple[str, DiscoveryIngestionResult]] = {}

    def ingest_batch(self, batch: DiscoveryBatch) -> DiscoveryIngestionResult:
        digest = discovery_batch_sha256(batch)
        existing_batch = self.batches.get(batch.batch_id)
        if existing_batch is not None:
            if existing_batch[0] != digest:
                raise DuplicateDiscoveryBatch(
                    f"discovery batch {batch.batch_id} already contains different content"
                )
            return existing_batch[1]

        created_count = 0
        frontier_ids: list[UUID] = []
        for candidate in batch.records:
            frontier_id = self._resolve(candidate)
            if frontier_id is None:
                frontier_id = uuid4()
                record = self._new_record(frontier_id, batch, candidate)
                self.records[frontier_id] = record
                self.urls[candidate.canonical_url] = frontier_id
                created_count += 1
            else:
                record = self.records[frontier_id]
                if record.entity_kind is not candidate.entity_kind:
                    raise InvalidDiscoveryRecord(
                        "a canonical URL cannot represent different discovery entity kinds"
                    )
                record = self._rediscover(record, batch, candidate)
                self.records[frontier_id] = record
            self.identities[candidate.identity_key] = frontier_id
            frontier_ids.append(frontier_id)

        for candidate, frontier_id in zip(batch.records, frontier_ids, strict=True):
            relationships = list(self.records[frontier_id].relationships)
            for relationship in candidate.relationships:
                related_id = self.identities.get(relationship.related_identity_key)
                if related_id is None:
                    raise InvalidDiscoveryRecord(
                        "related discovery identity "
                        f"{relationship.related_identity_key!r} is unknown"
                    )
                item = CrawlFrontierRelationship(
                    related_frontier_id=related_id,
                    related_identity_key=relationship.related_identity_key,
                    kind=relationship.kind,
                    created_at=batch.completed_at,
                )
                if item not in relationships:
                    relationships.append(item)
            self.records[frontier_id] = self.records[frontier_id].model_copy(
                update={"relationships": tuple(relationships)}
            )

        result = DiscoveryIngestionResult(
            batch_id=batch.batch_id,
            created_count=created_count,
            deduplicated_count=len(batch.records) - created_count,
            records=tuple(self.records[item] for item in frontier_ids),
        )
        self.batches[batch.batch_id] = digest, result
        return result

    def get(self, frontier_id: UUID) -> CrawlFrontierRecord | None:
        return self.records.get(frontier_id)

    def list(
        self,
        filters: CrawlFrontierFilters,
        *,
        limit: int,
        offset: int,
    ) -> tuple[Sequence[CrawlFrontierRecord], int]:
        records = [record for record in self.records.values() if _matches(record, filters)]
        records.sort(
            key=lambda record: (
                -record.priority,
                record.next_eligible_fetch_at,
                str(record.frontier_id),
            )
        )
        return tuple(records[offset : offset + limit]), len(records)

    def update_schedule(
        self,
        frontier_id: UUID,
        *,
        expected_version: int,
        priority: int,
        next_eligible_fetch_at: datetime,
        updated_at: datetime,
    ) -> CrawlFrontierRecord:
        current = self._required(frontier_id)
        self._require_version(current, expected_version)
        if current.status is CrawlFrontierStatus.LEASED:
            raise InvalidFrontierState("cannot reschedule a leased frontier record")
        updated = current.model_copy(
            update={
                "status": (
                    CrawlFrontierStatus.PENDING
                    if current.status is CrawlFrontierStatus.ACQUIRED
                    else current.status
                ),
                "priority": priority,
                "next_eligible_fetch_at": next_eligible_fetch_at,
                "version": current.version + 1,
                "updated_at": updated_at,
            }
        )
        self.records[frontier_id] = updated
        return updated

    def retry(
        self,
        frontier_id: UUID,
        *,
        expected_version: int,
        next_eligible_fetch_at: datetime,
        rationale: str,
        updated_at: datetime,
    ) -> CrawlFrontierRecord:
        current = self._required(frontier_id)
        self._require_version(current, expected_version)
        if not rationale:
            raise InvalidFrontierState("manual retry requires a rationale")
        if current.status not in {
            CrawlFrontierStatus.PENDING,
            CrawlFrontierStatus.RETRY_WAIT,
            CrawlFrontierStatus.DEAD_LETTER,
        }:
            raise InvalidFrontierState(f"cannot retry a {current.status.value} frontier record")
        updated = current.model_copy(
            update={
                "status": CrawlFrontierStatus.PENDING,
                "next_eligible_fetch_at": next_eligible_fetch_at,
                "lease": None,
                "version": current.version + 1,
                "updated_at": updated_at,
            }
        )
        self.records[frontier_id] = updated
        return updated

    def record_acquisition(
        self,
        frontier_id: UUID,
        *,
        expected_version: int,
        artifact: ArtifactReference,
        trace_id: UUID,
        acquired_at: datetime,
    ) -> CrawlFrontierRecord:
        current = self._required(frontier_id)
        if any(item.artifact.artifact_id == artifact.artifact_id for item in current.acquisitions):
            return current
        self._require_version(current, expected_version)
        if current.status in {CrawlFrontierStatus.LEASED, CrawlFrontierStatus.PROHIBITED}:
            raise InvalidFrontierState(
                f"cannot publish acquisition for a {current.status.value} frontier record"
            )
        acquisition = DiscoveryArtifactAcquisition(
            artifact=artifact,
            trace_id=trace_id,
            acquired_at=acquired_at,
        )
        updated = current.model_copy(
            update={
                "status": CrawlFrontierStatus.ACQUIRED,
                "lease": None,
                "acquisitions": (*current.acquisitions, acquisition),
                "version": current.version + 1,
                "updated_at": acquired_at,
            }
        )
        self.records[frontier_id] = updated
        return updated

    def _resolve(self, candidate: DiscoveryRecord) -> UUID | None:
        by_identity = self.identities.get(candidate.identity_key)
        by_url = self.urls.get(candidate.canonical_url)
        if by_identity is not None and by_url is not None and by_identity != by_url:
            raise InvalidDiscoveryRecord(
                "discovery identity and canonical URL resolve to different frontier records"
            )
        return by_identity or by_url

    def _new_record(
        self,
        frontier_id: UUID,
        batch: DiscoveryBatch,
        candidate: DiscoveryRecord,
    ) -> CrawlFrontierRecord:
        status = (
            CrawlFrontierStatus.PROHIBITED
            if candidate.access_status is DiscoveryAccessStatus.PROHIBITED
            else CrawlFrontierStatus.PENDING
            if candidate.access_status is DiscoveryAccessStatus.ALLOWED
            and candidate.expected_media_types
            else CrawlFrontierStatus.UNSUPPORTED
        )
        return CrawlFrontierRecord(
            frontier_id=frontier_id,
            identity_key=candidate.identity_key,
            primary_source=ToolIdentifier(name=batch.source.name, version=batch.source.version),
            source_record_id=candidate.source_record_id,
            accession=candidate.accession,
            entity_kind=candidate.entity_kind,
            canonical_url=candidate.canonical_url,
            acquisition_method=candidate.acquisition_method,
            status=status,
            priority=candidate.priority,
            source_updated_at=candidate.source_updated_at,
            discovered_at=candidate.discovered_at,
            last_discovered_at=candidate.discovered_at,
            next_eligible_fetch_at=candidate.next_eligible_fetch_at,
            attempt_count=0,
            robots_status=candidate.robots_status,
            expected_media_types=candidate.expected_media_types,
            license=candidate.license,
            access_status=candidate.access_status,
            access_reason=candidate.access_reason,
            trace_id=candidate.trace_id,
            last_discovery_batch_id=batch.batch_id,
            version=1,
            created_at=candidate.discovered_at,
            updated_at=candidate.discovered_at,
        )

    def _rediscover(
        self,
        current: CrawlFrontierRecord,
        batch: DiscoveryBatch,
        candidate: DiscoveryRecord,
    ) -> CrawlFrontierRecord:
        prohibited = (
            current.access_status is DiscoveryAccessStatus.PROHIBITED
            or candidate.access_status is DiscoveryAccessStatus.PROHIBITED
        )
        media_types = tuple(
            dict.fromkeys((*current.expected_media_types, *candidate.expected_media_types))
        )
        status = current.status
        if prohibited:
            status = CrawlFrontierStatus.PROHIBITED
        elif (
            status is CrawlFrontierStatus.UNSUPPORTED
            and media_types
            and candidate.access_status is DiscoveryAccessStatus.ALLOWED
        ):
            status = CrawlFrontierStatus.PENDING
        return current.model_copy(
            update={
                "status": status,
                "last_discovered_at": max(current.last_discovered_at, candidate.discovered_at),
                "expected_media_types": media_types,
                "access_status": (
                    DiscoveryAccessStatus.PROHIBITED if prohibited else candidate.access_status
                ),
                "access_reason": (
                    candidate.access_reason if candidate.access_reason else current.access_reason
                ),
                "trace_id": candidate.trace_id,
                "last_discovery_batch_id": batch.batch_id,
                "version": current.version + 1,
                "updated_at": max(current.updated_at, candidate.discovered_at),
            }
        )

    def _required(self, frontier_id: UUID) -> CrawlFrontierRecord:
        try:
            return self.records[frontier_id]
        except KeyError as exc:
            raise FrontierNotFound(f"frontier record {frontier_id} does not exist") from exc

    @staticmethod
    def _require_version(record: CrawlFrontierRecord, expected_version: int) -> None:
        if record.version != expected_version:
            raise FrontierConcurrencyConflict(
                f"frontier record changed from version {expected_version} to {record.version}"
            )


def _matches(record: CrawlFrontierRecord, filters: CrawlFrontierFilters) -> bool:
    return not (
        (filters.status is not None and record.status is not filters.status)
        or (filters.entity_kind is not None and record.entity_kind is not filters.entity_kind)
        or (filters.source_name is not None and record.primary_source.name != filters.source_name)
        or (filters.access_status is not None and record.access_status is not filters.access_status)
        or (
            filters.missing_artifact is not None
            and filters.missing_artifact == bool(record.acquisitions)
        )
    )
