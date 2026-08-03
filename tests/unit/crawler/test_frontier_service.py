from __future__ import annotations

from datetime import UTC, datetime, timedelta
from uuid import UUID, uuid4

import pytest
from hiveblot_contracts import (
    ArtifactReference,
    CrawlFrontierFilters,
    CrawlFrontierStatus,
    DiscoveryAccessStatus,
    DiscoveryAcquisitionMethod,
    DiscoveryBatch,
    DiscoveryEntityKind,
    DiscoveryEvidence,
    DiscoveryLicense,
    DiscoveryLicenseStatus,
    DiscoveryQuery,
    DiscoveryRecord,
    DiscoveryRelationship,
    DiscoveryRelationshipKind,
    DiscoveryRobotsStatus,
    ToolIdentifier,
)
from hiveblot_crawler import (
    DuplicateDiscoveryBatch,
    FrontierConcurrencyConflict,
    FrontierService,
)

from tests.fakes.discovery import InMemoryFrontierRepository

NOW = datetime(2026, 8, 2, 12, tzinfo=UTC)
TRACE_ID = UUID("00000000-0000-0000-0000-000000000123")


def _candidate(
    identity: str,
    *,
    url: str | None = None,
    priority: int = 100,
    relationships: tuple[DiscoveryRelationship, ...] = (),
    access_status: DiscoveryAccessStatus = DiscoveryAccessStatus.ALLOWED,
) -> DiscoveryRecord:
    return DiscoveryRecord(
        identity_key=identity,
        source_record_id=f"source:{identity}",
        entity_kind=(
            DiscoveryEntityKind.METADATA_RECORD
            if identity.startswith("metadata:")
            else DiscoveryEntityKind.PAPER
        ),
        canonical_url=url or f"https://example.test/{identity}",
        acquisition_method=DiscoveryAcquisitionMethod.OFFICIAL_API,
        priority=priority,
        discovered_at=NOW,
        next_eligible_fetch_at=NOW + timedelta(hours=priority % 3),
        robots_status=DiscoveryRobotsStatus.NOT_APPLICABLE,
        expected_media_types=()
        if access_status is DiscoveryAccessStatus.PROHIBITED
        else ("application/xml",),
        license=DiscoveryLicense(
            status=DiscoveryLicenseStatus.UNVERIFIED,
            statement="article-level verification required",
        ),
        access_status=access_status,
        access_reason=(
            "source prohibited" if access_status is DiscoveryAccessStatus.PROHIBITED else None
        ),
        relationships=relationships,
        trace_id=TRACE_ID,
    )


def _batch(*records: DiscoveryRecord, batch_id: UUID | None = None) -> DiscoveryBatch:
    return DiscoveryBatch(
        batch_id=batch_id or uuid4(),
        source=ToolIdentifier(name="fixture-source", version="1.0.0"),
        query=DiscoveryQuery(maximum_pages=1),
        trace_id=TRACE_ID,
        started_at=NOW,
        completed_at=NOW,
        records=records,
        evidence=(
            DiscoveryEvidence(
                request_url="https://example.test/discovery",
                response_artifact=ArtifactReference(
                    artifact_id=uuid4(),
                    sha256="a" * 64,
                    media_type="application/xml",
                    byte_size=1,
                ),
                response_sha256="a" * 64,
                response_media_type="application/xml",
                http_status=200,
                fetched_at=NOW,
            ),
        ),
    )


def test_frontier_deduplicates_identity_and_canonical_url_and_preserves_relationships() -> None:
    repository = InMemoryFrontierRepository()
    service = FrontierService(repository, clock=lambda: NOW)
    paper = _candidate("paper:123", url="HTTPS://EXAMPLE.TEST:443/paper#section")
    metadata = _candidate(
        "metadata:123",
        relationships=(
            DiscoveryRelationship(
                related_identity_key="paper:123",
                kind=DiscoveryRelationshipKind.DESCRIBES,
            ),
        ),
    )

    initial = service.ingest(_batch(paper, metadata))
    rediscovered = service.ingest(
        _batch(_candidate("paper-alias:123", url="https://example.test/paper"))
    )

    assert initial.created_count == 2
    assert initial.records[0].canonical_url == "https://example.test/paper"
    assert initial.records[1].relationships[0].related_frontier_id == initial.records[0].frontier_id
    assert rediscovered.created_count == 0
    assert rediscovered.records[0].frontier_id == initial.records[0].frontier_id
    assert repository.identities["paper-alias:123"] == initial.records[0].frontier_id


def test_frontier_batch_replay_is_idempotent_but_identity_reuse_is_not() -> None:
    repository = InMemoryFrontierRepository()
    service = FrontierService(repository, clock=lambda: NOW)
    batch_id = uuid4()
    batch = _batch(_candidate("paper:123"), batch_id=batch_id)
    first = service.ingest(batch)
    second = service.ingest(batch)
    assert second == first

    changed = _batch(_candidate("paper:456"), batch_id=batch_id)
    with pytest.raises(DuplicateDiscoveryBatch):
        service.ingest(changed)


def test_frontier_browse_priority_delay_and_optimistic_schedule_are_explicit() -> None:
    repository = InMemoryFrontierRepository()
    service = FrontierService(repository, clock=lambda: NOW + timedelta(minutes=1))
    result = service.ingest(
        _batch(
            _candidate("paper:low", priority=10),
            _candidate("paper:high", priority=900),
            _candidate(
                "paper:prohibited",
                priority=1000,
                access_status=DiscoveryAccessStatus.PROHIBITED,
            ),
        )
    )

    pending = service.browse(
        CrawlFrontierFilters(status=CrawlFrontierStatus.PENDING),
        limit=10,
        offset=0,
    )
    assert [record.identity_key for record in pending.items] == ["paper:high", "paper:low"]
    updated = service.update_schedule(
        result.records[0].frontier_id,
        expected_version=1,
        priority=500,
        next_eligible_fetch_at=NOW + timedelta(days=2),
    )
    assert updated.priority == 500
    assert updated.version == 2
    with pytest.raises(FrontierConcurrencyConflict):
        service.update_schedule(
            updated.frontier_id,
            expected_version=1,
            priority=1,
            next_eligible_fetch_at=NOW,
        )


def test_frontier_pagination_is_stable() -> None:
    service = FrontierService(InMemoryFrontierRepository(), clock=lambda: NOW)
    service.ingest(_batch(*(_candidate(f"paper:{index}") for index in range(5))))
    first = service.browse(CrawlFrontierFilters(), limit=2, offset=0)
    second = service.browse(CrawlFrontierFilters(), limit=2, offset=first.next_offset or 0)
    assert first.total == 5
    assert first.next_offset == 2
    assert second.offset == 2
    assert len({item.frontier_id for item in (*first.items, *second.items)}) == 4
