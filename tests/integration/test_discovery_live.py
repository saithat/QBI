"""Opt-in PostgreSQL acceptance test for PRD-015 frontier persistence."""

from __future__ import annotations

import os
from datetime import UTC, datetime, timedelta
from uuid import UUID, uuid4

import psycopg
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
    FrontierConcurrencyConflict,
    FrontierService,
    PostgresFrontierRepository,
)

from hiveblot import db
from hiveblot.settings import Settings

pytestmark = pytest.mark.skipif(
    os.getenv("HIVEBLOT_RUN_LIVE_DISCOVERY") != "1",
    reason="set HIVEBLOT_RUN_LIVE_DISCOVERY=1 with local PostgreSQL",
)


def test_postgres_frontier_dedupe_relationships_mutations_and_acquisition() -> None:
    settings = Settings(_env_file=None)
    db.initialize(settings.database_url)
    repository = PostgresFrontierRepository(settings.database_url)
    service = FrontierService(repository, clock=lambda: NOW + timedelta(minutes=1))
    test_id = uuid4().hex
    batch_id = uuid4()
    trace_id = uuid4()
    evidence_artifact_id = uuid4()
    evidence_blob_hash = uuid4().hex + uuid4().hex
    artifact_id = uuid4()
    blob_hash = uuid4().hex + uuid4().hex
    paper_identity = f"live-paper:{test_id}"
    metadata_identity = f"live-metadata:{test_id}"
    evidence_artifact = ArtifactReference(
        artifact_id=evidence_artifact_id,
        sha256=evidence_blob_hash,
        media_type="application/xml",
        byte_size=1,
    )
    batch = _batch(
        batch_id,
        trace_id,
        evidence_artifact,
        _record(paper_identity, trace_id=trace_id),
        _record(
            metadata_identity,
            trace_id=trace_id,
            kind=DiscoveryEntityKind.METADATA_RECORD,
            relationships=(
                DiscoveryRelationship(
                    related_identity_key=paper_identity,
                    kind=DiscoveryRelationshipKind.DESCRIBES,
                ),
            ),
        ),
    )
    try:
        _insert_artifact(
            settings.database_url,
            artifact_id=evidence_artifact_id,
            blob_hash=evidence_blob_hash,
            storage_key=f"live-discovery-evidence/{test_id}",
            filename=f"{test_id}-evidence.xml",
        )
        first = service.ingest(batch)
        replay = service.ingest(batch)
        assert replay == first
        assert first.created_count == 2
        assert first.records[1].relationships[0].related_frontier_id == first.records[0].frontier_id

        alias_batch = _batch(
            uuid4(),
            trace_id,
            evidence_artifact,
            _record(
                f"live-paper-alias:{test_id}",
                trace_id=trace_id,
                url=first.records[0].canonical_url,
            ),
        )
        alias = service.ingest(alias_batch)
        assert alias.created_count == 0
        assert alias.records[0].frontier_id == first.records[0].frontier_id

        scheduled = service.update_schedule(
            first.records[0].frontier_id,
            expected_version=alias.records[0].version,
            priority=900,
            next_eligible_fetch_at=NOW + timedelta(days=1),
        )
        with pytest.raises(FrontierConcurrencyConflict):
            service.update_schedule(
                scheduled.frontier_id,
                expected_version=first.records[0].version,
                priority=1,
                next_eligible_fetch_at=NOW,
            )

        with psycopg.connect(settings.database_url) as connection:
            connection.execute(
                """
                INSERT INTO artifact_blobs (
                    sha256, media_type, byte_size, storage_key, created_at
                ) VALUES (%s, 'application/xml', 123, %s, %s)
                """,
                (blob_hash, f"live-discovery/{test_id}", NOW),
            )
            connection.execute(
                """
                INSERT INTO artifacts (
                    artifact_id, blob_sha256, original_filename, source_uri,
                    acquisition_method, visibility, organization_id, created_at
                ) VALUES (%s, %s, %s, %s, 'source_adapter', 'public', NULL, %s)
                """,
                (
                    artifact_id,
                    blob_hash,
                    f"{test_id}.xml",
                    scheduled.canonical_url,
                    NOW,
                ),
            )
        acquired = repository.record_acquisition(
            scheduled.frontier_id,
            expected_version=scheduled.version,
            artifact=ArtifactReference(
                artifact_id=artifact_id,
                sha256=blob_hash,
                media_type="application/xml",
                byte_size=123,
            ),
            trace_id=trace_id,
            acquired_at=NOW + timedelta(minutes=2),
        )
        assert acquired.status is CrawlFrontierStatus.ACQUIRED
        assert acquired.acquisitions[0].artifact.artifact_id == artifact_id
        assert (
            service.browse(
                CrawlFrontierFilters(missing_artifact=False),
                limit=200,
                offset=0,
            ).total
            >= 1
        )
    finally:
        _cleanup(
            settings.database_url,
            identities=(paper_identity, metadata_identity, f"live-paper-alias:{test_id}"),
            artifact_id=artifact_id,
            blob_hash=blob_hash,
            evidence_artifact_id=evidence_artifact_id,
            evidence_blob_hash=evidence_blob_hash,
        )


NOW = datetime(2026, 8, 2, 12, tzinfo=UTC)


def _record(
    identity: str,
    *,
    trace_id: UUID,
    kind: DiscoveryEntityKind = DiscoveryEntityKind.PAPER,
    url: str | None = None,
    relationships: tuple[DiscoveryRelationship, ...] = (),
) -> DiscoveryRecord:
    return DiscoveryRecord(
        identity_key=identity,
        source_record_id=f"source:{identity}",
        entity_kind=kind,
        canonical_url=url or f"https://example.test/discovery/{identity}",
        acquisition_method=DiscoveryAcquisitionMethod.OFFICIAL_API,
        discovered_at=NOW,
        next_eligible_fetch_at=NOW,
        robots_status=DiscoveryRobotsStatus.NOT_APPLICABLE,
        expected_media_types=("application/xml",),
        license=DiscoveryLicense(
            status=DiscoveryLicenseStatus.UNVERIFIED,
            statement="live test license verification required",
        ),
        access_status=DiscoveryAccessStatus.ALLOWED,
        relationships=relationships,
        trace_id=trace_id,
    )


def _batch(
    batch_id: UUID,
    trace_id: UUID,
    response_artifact: ArtifactReference,
    *records: DiscoveryRecord,
) -> DiscoveryBatch:
    return DiscoveryBatch(
        batch_id=batch_id,
        source=ToolIdentifier(name="live-fixture", version="1.0.0"),
        query=DiscoveryQuery(maximum_pages=1),
        trace_id=trace_id,
        started_at=NOW,
        completed_at=NOW,
        records=records,
        evidence=(
            DiscoveryEvidence(
                request_url="https://example.test/live-discovery",
                response_artifact=response_artifact,
                response_sha256=response_artifact.sha256,
                response_media_type="application/xml",
                http_status=200,
                fetched_at=NOW,
            ),
        ),
    )


def _cleanup(
    database_url: str,
    *,
    identities: tuple[str, ...],
    artifact_id: UUID,
    blob_hash: str,
    evidence_artifact_id: UUID,
    evidence_blob_hash: str,
) -> None:
    with psycopg.connect(database_url) as connection:
        frontier_ids = [
            row[0]
            for row in connection.execute(
                """
                SELECT DISTINCT frontier_id FROM crawl_frontier_aliases
                WHERE identity_key = ANY(%s)
                """,
                (list(identities),),
            ).fetchall()
        ]
        batch_ids = (
            [
                row[0]
                for row in connection.execute(
                    """
                    SELECT DISTINCT batch_id FROM discovery_observations
                    WHERE frontier_id = ANY(%s)
                    """,
                    (frontier_ids,),
                ).fetchall()
            ]
            if frontier_ids
            else []
        )
        if frontier_ids:
            connection.execute(
                "DELETE FROM crawl_frontier_events WHERE frontier_id = ANY(%s)",
                (frontier_ids,),
            )
            connection.execute(
                "DELETE FROM crawl_frontier_artifacts WHERE frontier_id = ANY(%s)",
                (frontier_ids,),
            )
            connection.execute(
                """
                DELETE FROM crawl_frontier_relationships
                WHERE frontier_id = ANY(%s) OR related_frontier_id = ANY(%s)
                """,
                (frontier_ids, frontier_ids),
            )
            connection.execute(
                "DELETE FROM discovery_observations WHERE frontier_id = ANY(%s)",
                (frontier_ids,),
            )
            connection.execute(
                "DELETE FROM crawl_frontier_aliases WHERE frontier_id = ANY(%s)",
                (frontier_ids,),
            )
            connection.execute(
                "DELETE FROM crawl_frontier WHERE frontier_id = ANY(%s)",
                (frontier_ids,),
            )
        if batch_ids:
            connection.execute(
                "DELETE FROM discovery_run_evidence WHERE batch_id = ANY(%s)",
                (batch_ids,),
            )
            connection.execute(
                "DELETE FROM discovery_runs WHERE batch_id = ANY(%s)",
                (batch_ids,),
            )
        connection.execute("DELETE FROM artifacts WHERE artifact_id = %s", (artifact_id,))
        connection.execute(
            "DELETE FROM artifacts WHERE artifact_id = %s",
            (evidence_artifact_id,),
        )
        connection.execute("DELETE FROM artifact_blobs WHERE sha256 = %s", (blob_hash,))
        connection.execute(
            "DELETE FROM artifact_blobs WHERE sha256 = %s",
            (evidence_blob_hash,),
        )


def _insert_artifact(
    database_url: str,
    *,
    artifact_id: UUID,
    blob_hash: str,
    storage_key: str,
    filename: str,
) -> None:
    with psycopg.connect(database_url) as connection:
        connection.execute(
            """
            INSERT INTO artifact_blobs (
                sha256, media_type, byte_size, storage_key, created_at
            ) VALUES (%s, 'application/xml', 1, %s, %s)
            """,
            (blob_hash, storage_key, NOW),
        )
        connection.execute(
            """
            INSERT INTO artifacts (
                artifact_id, blob_sha256, original_filename, source_uri,
                acquisition_method, visibility, organization_id, created_at
            ) VALUES (%s, %s, %s, %s, 'source_adapter', 'public', NULL, %s)
            """,
            (
                artifact_id,
                blob_hash,
                filename,
                f"urn:hiveblot:test:discovery-evidence:{artifact_id}",
                NOW,
            ),
        )
