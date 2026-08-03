from __future__ import annotations

from datetime import UTC, datetime
from uuid import uuid4

import pytest
from hiveblot_contracts import (
    ArtifactReference,
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
    DiscoveryRobotsStatus,
    ToolIdentifier,
    discovery_batch_sha256,
)
from pydantic import ValidationError

NOW = datetime(2026, 8, 2, 12, tzinfo=UTC)


def _record() -> DiscoveryRecord:
    trace_id = uuid4()
    return DiscoveryRecord(
        identity_key="pmc:pmc123",
        source_record_id="oai:pubmedcentral.nih.gov:123",
        accession="PMC123",
        entity_kind=DiscoveryEntityKind.PAPER,
        canonical_url=(
            "https://pmc.ncbi.nlm.nih.gov/api/oai/v1/mh/?verb=GetRecord"
            "&identifier=oai%3Apubmedcentral.nih.gov%3A123&metadataPrefix=pmc"
        ),
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
        trace_id=trace_id,
    )


def test_discovery_contracts_are_strict_frozen_and_reject_unknown_fields() -> None:
    record = _record()
    with pytest.raises(ValidationError):
        DiscoveryRecord.model_validate(
            {**record.model_dump(), "priority": "100"},
            strict=True,
        )
    with pytest.raises(ValidationError):
        DiscoveryRecord.model_validate(
            {**record.model_dump(), "unknown": True},
            strict=True,
        )
    with pytest.raises(ValidationError):
        record.priority = 99  # type: ignore[misc]


def test_prohibited_records_and_verified_licenses_have_explicit_evidence() -> None:
    record = _record()
    with pytest.raises(ValidationError, match="access reason"):
        DiscoveryRecord.model_validate(
            {
                **record.model_dump(),
                "access_status": DiscoveryAccessStatus.PROHIBITED,
                "access_reason": None,
            },
            strict=True,
        )
    with pytest.raises(ValidationError, match="requires an identifier or URI"):
        DiscoveryLicense(
            status=DiscoveryLicenseStatus.VERIFIED,
            verification_required=False,
        )
    with pytest.raises(ValidationError, match="HTTP"):
        DiscoveryEvidence(
            request_url="file:///tmp/source.xml",
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
        )
    with pytest.raises(ValidationError, match="artifact hash"):
        DiscoveryEvidence(
            request_url="https://example.test/oai",
            response_artifact=ArtifactReference(
                artifact_id=uuid4(),
                sha256="b" * 64,
                media_type="application/xml",
                byte_size=1,
            ),
            response_sha256="a" * 64,
            response_media_type="application/xml",
            http_status=200,
            fetched_at=NOW,
        )


def test_discovery_batch_requires_trace_consistency_and_has_stable_digest() -> None:
    record = _record()
    batch = DiscoveryBatch(
        batch_id=uuid4(),
        source=ToolIdentifier(name="pmc-oai", version="1.0.0"),
        query=DiscoveryQuery(from_date=NOW.date(), maximum_pages=1),
        trace_id=record.trace_id,
        started_at=NOW,
        completed_at=NOW,
        records=(record,),
        evidence=(
            DiscoveryEvidence(
                request_url="https://example.test/oai",
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
    assert discovery_batch_sha256(batch) == discovery_batch_sha256(batch)
    assert len(discovery_batch_sha256(batch)) == 64
    with pytest.raises(ValidationError, match="trace IDs"):
        DiscoveryBatch.model_validate(
            {
                **batch.model_dump(),
                "trace_id": uuid4(),
            },
            strict=True,
        )


def test_frontier_status_surface_is_explicit() -> None:
    assert {item.value for item in CrawlFrontierStatus} == {
        "pending",
        "leased",
        "acquired",
        "retry_wait",
        "prohibited",
        "unsupported",
        "dead_letter",
    }
