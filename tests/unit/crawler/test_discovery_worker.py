from __future__ import annotations

from datetime import UTC, date, datetime
from uuid import UUID, uuid4

import pytest
from hiveblot_contracts import (
    ArtifactReference,
    DiscoveryAccessStatus,
    DiscoveryAcquisitionMethod,
    DiscoveryBatch,
    DiscoveryEntityKind,
    DiscoveryEvidence,
    DiscoveryLicense,
    DiscoveryLicenseStatus,
    DiscoveryQuery,
    DiscoveryRecord,
    DiscoveryRobotsStatus,
    ToolIdentifier,
)
from hiveblot_crawler import FrontierService

from hiveblot.settings import DiscoverySettings
from tests.fakes.discovery import InMemoryFrontierRepository
from workers.discovery.entrypoint import run_discovery, scheduled_query

NOW = datetime(2026, 8, 2, 12, tzinfo=UTC)
TRACE_ID = UUID("00000000-0000-0000-0000-000000000123")


class _Adapter:
    name = "fixture"
    version = "1.0.0"

    def discover(
        self,
        query: DiscoveryQuery,
        *,
        trace_id: UUID | None = None,
    ) -> DiscoveryBatch:
        del query
        actual_trace_id = trace_id or TRACE_ID
        record = DiscoveryRecord(
            identity_key="pmc:pmc123",
            source_record_id="oai:pubmedcentral.nih.gov:123",
            accession="PMC123",
            entity_kind=DiscoveryEntityKind.PAPER,
            canonical_url="https://example.test/PMC123.xml",
            acquisition_method=DiscoveryAcquisitionMethod.OFFICIAL_API,
            discovered_at=NOW,
            next_eligible_fetch_at=NOW,
            robots_status=DiscoveryRobotsStatus.NOT_APPLICABLE,
            expected_media_types=("application/xml",),
            license=DiscoveryLicense(
                status=DiscoveryLicenseStatus.UNVERIFIED,
                statement="verify article-level license",
            ),
            access_status=DiscoveryAccessStatus.ALLOWED,
            trace_id=actual_trace_id,
        )
        return DiscoveryBatch(
            batch_id=uuid4(),
            source=ToolIdentifier(name=self.name, version=self.version),
            query=DiscoveryQuery(maximum_pages=1),
            trace_id=actual_trace_id,
            started_at=NOW,
            completed_at=NOW,
            records=(record,),
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
            next_cursor="next-page",
        )


def test_scheduled_query_uses_bounded_lookback_and_supports_manual_resume() -> None:
    settings = DiscoverySettings(
        discovery_lookback_days=2,
        discovery_maximum_pages=7,
        _env_file=None,
    )
    query = scheduled_query(
        settings,
        today=date(2026, 8, 2),
        from_date=None,
        until_date=None,
        cursor=None,
        maximum_pages=None,
    )
    assert query.from_date == date(2026, 7, 31)
    assert query.until_date == date(2026, 8, 2)
    assert query.maximum_pages == 7

    resumed = scheduled_query(
        settings,
        today=date(2026, 8, 2),
        from_date=None,
        until_date=None,
        cursor="opaque-token",
        maximum_pages=2,
    )
    assert resumed.cursor == "opaque-token"
    assert resumed.maximum_pages == 2
    with pytest.raises(ValueError, match="cannot be combined"):
        scheduled_query(
            settings,
            today=date(2026, 8, 2),
            from_date=date(2026, 8, 1),
            until_date=None,
            cursor="opaque-token",
            maximum_pages=None,
        )


def test_discovery_worker_emits_traceable_summary_and_populates_frontier() -> None:
    repository = InMemoryFrontierRepository()
    summary = run_discovery(
        _Adapter(),
        FrontierService(repository, clock=lambda: NOW),
        DiscoveryQuery(maximum_pages=1),
    )
    assert summary["created_count"] == 1
    assert summary["record_count"] == 1
    assert summary["evidence_count"] == 1
    assert summary["next_cursor"] == "next-page"
    assert len(repository.records) == 1
