"""Bounded PMC OAI discovery command used locally and by a Kubernetes CronJob."""

from __future__ import annotations

import argparse
import json
from collections.abc import Sequence
from datetime import UTC, date, datetime, timedelta

from hiveblot_contracts import DiscoveryQuery
from hiveblot_crawler import (
    ArtifactDiscoveryEvidencePublisher,
    DiscoveryAdapter,
    FrontierService,
    PmcOaiDiscoveryAdapter,
    PostgresFrontierRepository,
)
from hiveblot_storage import (
    ArtifactService,
    PostgresArtifactRepository,
    S3ObjectStore,
    SourceAdapterRegistry,
)

from hiveblot import db
from hiveblot.settings import DiscoverySettings


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Discover PMC open-set records through the official OAI-PMH API",
    )
    parser.add_argument("--from-date", type=date.fromisoformat)
    parser.add_argument("--until-date", type=date.fromisoformat)
    parser.add_argument("--cursor")
    parser.add_argument("--maximum-pages", type=int)
    return parser


def scheduled_query(
    settings: DiscoverySettings,
    *,
    today: date,
    from_date: date | None,
    until_date: date | None,
    cursor: str | None,
    maximum_pages: int | None,
) -> DiscoveryQuery:
    if cursor is not None and (from_date is not None or until_date is not None):
        raise ValueError("a continuation cursor cannot be combined with a date range")
    if cursor is not None:
        return DiscoveryQuery(
            cursor=cursor,
            maximum_pages=(
                settings.discovery_maximum_pages if maximum_pages is None else maximum_pages
            ),
        )
    end = until_date or today
    start = from_date or end - timedelta(days=settings.discovery_lookback_days)
    return DiscoveryQuery(
        from_date=start,
        until_date=end,
        maximum_pages=(
            settings.discovery_maximum_pages if maximum_pages is None else maximum_pages
        ),
    )


def run_discovery(
    adapter: DiscoveryAdapter,
    service: FrontierService,
    query: DiscoveryQuery,
) -> dict[str, object]:
    batch = adapter.discover(query)
    result = service.ingest(batch)
    return {
        "schema_version": "1.0",
        "batch_id": str(batch.batch_id),
        "source": batch.source.model_dump(mode="json"),
        "created_count": result.created_count,
        "deduplicated_count": result.deduplicated_count,
        "record_count": len(result.records),
        "evidence_count": len(batch.evidence),
        "next_cursor": batch.next_cursor,
        "trace_id": str(batch.trace_id),
    }


def main(argv: Sequence[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    settings = DiscoverySettings()
    query = scheduled_query(
        settings,
        today=datetime.now(UTC).date(),
        from_date=args.from_date,
        until_date=args.until_date,
        cursor=args.cursor,
        maximum_pages=args.maximum_pages,
    )
    db.initialize(settings.database_url)
    object_store = S3ObjectStore(
        endpoint_url=settings.s3_endpoint_url,
        region=settings.s3_region,
        bucket=settings.s3_bucket,
        access_key_id=settings.s3_access_key_id.get_secret_value(),
        secret_access_key=settings.s3_secret_access_key.get_secret_value(),
    )
    object_store.ensure_bucket()
    artifact_service = ArtifactService(
        repository=PostgresArtifactRepository(settings.database_url),
        object_store=object_store,
        source_adapters=SourceAdapterRegistry({}),
        max_bytes=settings.artifact_max_bytes,
        upload_url_seconds=3600,
        download_url_seconds=900,
    )
    adapter = PmcOaiDiscoveryAdapter(
        base_url=settings.pmc_oai_base_url,
        user_agent=settings.discovery_user_agent,
        evidence_publisher=ArtifactDiscoveryEvidencePublisher(artifact_service),
        timeout_seconds=settings.discovery_http_timeout_seconds,
        max_response_bytes=settings.discovery_max_response_bytes,
    )
    try:
        summary = run_discovery(
            adapter,
            FrontierService(PostgresFrontierRepository(settings.database_url)),
            query,
        )
    finally:
        adapter.close()
    print(json.dumps(summary, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
