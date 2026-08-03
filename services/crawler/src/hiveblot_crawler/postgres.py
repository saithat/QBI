"""PostgreSQL mapper for public discovery runs and the crawl frontier."""

from __future__ import annotations

import json
from collections.abc import Sequence
from datetime import datetime
from typing import Any
from uuid import UUID, uuid4

import psycopg
from hiveblot_contracts import (
    ArtifactReference,
    CrawlFrontierFilters,
    CrawlFrontierLease,
    CrawlFrontierRecord,
    CrawlFrontierRelationship,
    CrawlFrontierStatus,
    DiscoveryAccessStatus,
    DiscoveryAcquisitionMethod,
    DiscoveryArtifactAcquisition,
    DiscoveryBatch,
    DiscoveryEntityKind,
    DiscoveryIngestionResult,
    DiscoveryLicense,
    DiscoveryLicenseStatus,
    DiscoveryRecord,
    DiscoveryRobotsStatus,
    ToolIdentifier,
    discovery_batch_sha256,
)
from psycopg import errors
from psycopg.rows import dict_row
from psycopg.types.json import Jsonb
from pydantic import TypeAdapter

from .errors import (
    DuplicateDiscoveryBatch,
    FrontierConcurrencyConflict,
    FrontierNotFound,
    InvalidDiscoveryRecord,
    InvalidFrontierState,
)

_LICENSE_ADAPTER = TypeAdapter(DiscoveryLicense)
_RELATIONSHIPS_ADAPTER = TypeAdapter(tuple[CrawlFrontierRelationship, ...])
_ACQUISITIONS_ADAPTER = TypeAdapter(tuple[DiscoveryArtifactAcquisition, ...])

FRONTIER_SELECT = """
SELECT
    frontier.*,
    COALESCE(
        (
            SELECT JSONB_AGG(
                JSONB_BUILD_OBJECT(
                    'schema_version', '1.0',
                    'related_frontier_id', relationship.related_frontier_id,
                    'related_identity_key', related.identity_key,
                    'kind', relationship.relationship_kind,
                    'created_at', relationship.created_at
                )
                ORDER BY relationship.created_at, relationship.related_frontier_id,
                    relationship.relationship_kind
            )
            FROM crawl_frontier_relationships AS relationship
            JOIN crawl_frontier AS related
                ON related.frontier_id = relationship.related_frontier_id
            WHERE relationship.frontier_id = frontier.frontier_id
        ),
        '[]'::JSONB
    ) AS relationships_json,
    COALESCE(
        (
            SELECT JSONB_AGG(
                JSONB_BUILD_OBJECT(
                    'schema_version', '1.0',
                    'artifact', JSONB_BUILD_OBJECT(
                        'schema_version', '1.0',
                        'artifact_id', acquisition.artifact_id,
                        'sha256', blob.sha256,
                        'media_type', blob.media_type,
                        'byte_size', blob.byte_size
                    ),
                    'trace_id', acquisition.trace_id,
                    'acquired_at', acquisition.acquired_at
                )
                ORDER BY acquisition.acquired_at, acquisition.artifact_id
            )
            FROM crawl_frontier_artifacts AS acquisition
            JOIN artifacts AS artifact ON artifact.artifact_id = acquisition.artifact_id
            JOIN artifact_blobs AS blob ON blob.sha256 = artifact.blob_sha256
            WHERE acquisition.frontier_id = frontier.frontier_id
        ),
        '[]'::JSONB
    ) AS acquisitions_json
FROM crawl_frontier AS frontier
"""


class PostgresFrontierRepository:
    def __init__(self, database_url: str) -> None:
        self._database_url = database_url

    def ingest_batch(self, batch: DiscoveryBatch) -> DiscoveryIngestionResult:
        try:
            return self._ingest_batch_once(batch)
        except errors.UniqueViolation:
            try:
                return self._ingest_batch_once(batch)
            except errors.UniqueViolation as exc:
                raise InvalidDiscoveryRecord(
                    "discovery identity conflicts with canonical state"
                ) from exc
            except psycopg.IntegrityError as exc:
                raise InvalidDiscoveryRecord("discovery persistence constraint failed") from exc
        except psycopg.IntegrityError as exc:
            raise InvalidDiscoveryRecord("discovery persistence constraint failed") from exc

    def _ingest_batch_once(self, batch: DiscoveryBatch) -> DiscoveryIngestionResult:
        digest = discovery_batch_sha256(batch)
        try:
            with psycopg.connect(self._database_url, row_factory=dict_row) as connection:
                inserted = connection.execute(
                    """
                    INSERT INTO discovery_runs (
                        batch_id, source_name, source_version, trace_id, query_json,
                        batch_json, batch_sha256, started_at, completed_at
                    ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s)
                    ON CONFLICT (batch_id) DO NOTHING
                    RETURNING batch_id
                    """,
                    (
                        batch.batch_id,
                        batch.source.name,
                        batch.source.version,
                        batch.trace_id,
                        Jsonb(batch.query.model_dump(mode="json")),
                        Jsonb(batch.model_dump(mode="json")),
                        digest,
                        batch.started_at,
                        batch.completed_at,
                    ),
                ).fetchone()
                if inserted is None:
                    return self._existing_batch_result(connection, batch, digest)

                self._store_evidence(connection, batch)

                frontier_ids: list[UUID] = []
                created_count = 0
                for candidate in batch.records:
                    frontier_id, created = self._upsert_candidate(
                        connection,
                        batch,
                        candidate,
                    )
                    frontier_ids.append(frontier_id)
                    created_count += int(created)

                for candidate, frontier_id in zip(batch.records, frontier_ids, strict=True):
                    self._store_relationships(
                        connection,
                        candidate,
                        frontier_id=frontier_id,
                        created_at=batch.completed_at,
                    )

                deduplicated_count = len(batch.records) - created_count
                connection.execute(
                    """
                    UPDATE discovery_runs
                    SET created_count = %s, deduplicated_count = %s
                    WHERE batch_id = %s
                    """,
                    (created_count, deduplicated_count, batch.batch_id),
                )
                records = tuple(self._get_record(connection, item) for item in frontier_ids)
                return DiscoveryIngestionResult(
                    batch_id=batch.batch_id,
                    created_count=created_count,
                    deduplicated_count=deduplicated_count,
                    records=records,
                )
        except (DuplicateDiscoveryBatch, InvalidDiscoveryRecord):
            raise

    def get(self, frontier_id: UUID) -> CrawlFrontierRecord | None:
        with psycopg.connect(self._database_url, row_factory=dict_row) as connection:
            row = connection.execute(
                FRONTIER_SELECT + " WHERE frontier.frontier_id = %s",
                (frontier_id,),
            ).fetchone()
        return _frontier_record(row) if row is not None else None

    def list(
        self,
        filters: CrawlFrontierFilters,
        *,
        limit: int,
        offset: int,
    ) -> tuple[Sequence[CrawlFrontierRecord], int]:
        conditions: list[str] = []
        parameters: dict[str, object] = {"limit": limit, "offset": offset}
        if filters.status is not None:
            conditions.append("frontier.status = %(status)s")
            parameters["status"] = filters.status.value
        if filters.entity_kind is not None:
            conditions.append("frontier.entity_kind = %(entity_kind)s")
            parameters["entity_kind"] = filters.entity_kind.value
        if filters.source_name is not None:
            conditions.append("frontier.primary_source_name = %(source_name)s")
            parameters["source_name"] = filters.source_name
        if filters.access_status is not None:
            conditions.append("frontier.access_status = %(access_status)s")
            parameters["access_status"] = filters.access_status.value
        if filters.missing_artifact is not None:
            existence = "NOT EXISTS" if filters.missing_artifact else "EXISTS"
            conditions.append(
                existence
                + " (SELECT 1 FROM crawl_frontier_artifacts linked"
                + " WHERE linked.frontier_id = frontier.frontier_id)"
            )
        where = " WHERE " + " AND ".join(conditions) if conditions else ""
        with psycopg.connect(self._database_url, row_factory=dict_row) as connection:
            total_row = connection.execute(
                "SELECT COUNT(*) AS total FROM crawl_frontier AS frontier" + where,
                parameters,
            ).fetchone()
            rows = connection.execute(
                FRONTIER_SELECT
                + where
                + " ORDER BY frontier.priority DESC, frontier.next_eligible_fetch_at,"
                + " frontier.frontier_id LIMIT %(limit)s OFFSET %(offset)s",
                parameters,
            ).fetchall()
        assert total_row is not None
        return tuple(_frontier_record(row) for row in rows), int(total_row["total"])

    def update_schedule(
        self,
        frontier_id: UUID,
        *,
        expected_version: int,
        priority: int,
        next_eligible_fetch_at: datetime,
        updated_at: datetime,
    ) -> CrawlFrontierRecord:
        with psycopg.connect(self._database_url, row_factory=dict_row) as connection:
            current = self._locked(connection, frontier_id)
            self._require_version(current, expected_version)
            if CrawlFrontierStatus(current["status"]) is CrawlFrontierStatus.LEASED:
                raise InvalidFrontierState("cannot reschedule a leased frontier record")
            connection.execute(
                """
                UPDATE crawl_frontier
                SET status = CASE WHEN status = 'acquired' THEN 'pending' ELSE status END,
                    priority = %s,
                    next_eligible_fetch_at = %s,
                    version = version + 1,
                    updated_at = %s
                WHERE frontier_id = %s
                """,
                (priority, next_eligible_fetch_at, updated_at, frontier_id),
            )
            connection.execute(
                """
                UPDATE crawl_fetch_tasks
                SET priority = %s, next_eligible_at = %s, updated_at = %s
                WHERE frontier_id = %s AND status IN ('pending', 'retry_wait')
                """,
                (priority, next_eligible_fetch_at, updated_at, frontier_id),
            )
            self._event(
                connection,
                frontier_id,
                "schedule_updated",
                None,
                {
                    "previous_priority": current["priority"],
                    "priority": priority,
                    "next_eligible_fetch_at": next_eligible_fetch_at.isoformat(),
                },
                updated_at,
            )
            return self._get_record(connection, frontier_id)

    def retry(
        self,
        frontier_id: UUID,
        *,
        expected_version: int,
        next_eligible_fetch_at: datetime,
        rationale: str,
        updated_at: datetime,
    ) -> CrawlFrontierRecord:
        if not rationale:
            raise InvalidFrontierState("manual retry requires a rationale")
        with psycopg.connect(self._database_url, row_factory=dict_row) as connection:
            current = self._locked(connection, frontier_id)
            self._require_version(current, expected_version)
            current_status = CrawlFrontierStatus(current["status"])
            if current_status not in {
                CrawlFrontierStatus.PENDING,
                CrawlFrontierStatus.RETRY_WAIT,
                CrawlFrontierStatus.DEAD_LETTER,
            }:
                raise InvalidFrontierState(f"cannot retry a {current_status.value} frontier record")
            connection.execute(
                """
                UPDATE crawl_frontier
                SET status = 'pending',
                    next_eligible_fetch_at = %s,
                    lease_owner = NULL,
                    lease_token = NULL,
                    lease_expires_at = NULL,
                    version = version + 1,
                    updated_at = %s
                WHERE frontier_id = %s
                """,
                (next_eligible_fetch_at, updated_at, frontier_id),
            )
            connection.execute(
                """
                UPDATE crawl_fetch_tasks
                SET status = 'pending', next_eligible_at = %s,
                    updated_at = %s, completed_at = NULL
                WHERE frontier_id = %s AND status = 'retry_wait'
                """,
                (next_eligible_fetch_at, updated_at, frontier_id),
            )
            self._event(
                connection,
                frontier_id,
                "manual_retry",
                None,
                {"rationale": rationale, "previous_status": current_status.value},
                updated_at,
            )
            return self._get_record(connection, frontier_id)

    def record_acquisition(
        self,
        frontier_id: UUID,
        *,
        expected_version: int,
        artifact: ArtifactReference,
        trace_id: UUID,
        acquired_at: datetime,
    ) -> CrawlFrontierRecord:
        with psycopg.connect(self._database_url, row_factory=dict_row) as connection:
            current = self._locked(connection, frontier_id)
            existing = connection.execute(
                """
                SELECT 1 FROM crawl_frontier_artifacts
                WHERE frontier_id = %s AND artifact_id = %s
                """,
                (frontier_id, artifact.artifact_id),
            ).fetchone()
            if existing is not None:
                return self._get_record(connection, frontier_id)
            self._require_version(current, expected_version)
            current_status = CrawlFrontierStatus(current["status"])
            if current_status in {
                CrawlFrontierStatus.LEASED,
                CrawlFrontierStatus.PROHIBITED,
            }:
                raise InvalidFrontierState(
                    f"cannot publish acquisition for a {current_status.value} frontier record"
                )
            artifact_row = connection.execute(
                """
                SELECT artifact.artifact_id, artifact.visibility,
                    blob.sha256, blob.media_type, blob.byte_size
                FROM artifacts AS artifact
                JOIN artifact_blobs AS blob ON blob.sha256 = artifact.blob_sha256
                WHERE artifact.artifact_id = %s
                """,
                (artifact.artifact_id,),
            ).fetchone()
            if artifact_row is None:
                raise InvalidDiscoveryRecord(f"artifact {artifact.artifact_id} does not exist")
            if artifact_row["visibility"] != "public":
                raise InvalidDiscoveryRecord(
                    "public discovery frontier records require public acquired artifacts"
                )
            if (
                artifact_row["sha256"] != artifact.sha256
                or artifact_row["media_type"] != artifact.media_type
                or artifact_row["byte_size"] != artifact.byte_size
            ):
                raise InvalidDiscoveryRecord("artifact reference metadata does not match storage")
            connection.execute(
                """
                INSERT INTO crawl_frontier_artifacts (
                    frontier_id, artifact_id, trace_id, acquired_at
                ) VALUES (%s, %s, %s, %s)
                """,
                (frontier_id, artifact.artifact_id, trace_id, acquired_at),
            )
            connection.execute(
                """
                UPDATE crawl_frontier
                SET status = 'acquired',
                    lease_owner = NULL,
                    lease_token = NULL,
                    lease_expires_at = NULL,
                    version = version + 1,
                    updated_at = %s
                WHERE frontier_id = %s
                """,
                (acquired_at, frontier_id),
            )
            self._event(
                connection,
                frontier_id,
                "artifact_linked",
                trace_id,
                {"artifact_id": str(artifact.artifact_id), "sha256": artifact.sha256},
                acquired_at,
            )
            return self._get_record(connection, frontier_id)

    def _existing_batch_result(
        self,
        connection: psycopg.Connection[dict[str, Any]],
        batch: DiscoveryBatch,
        digest: str,
    ) -> DiscoveryIngestionResult:
        row = connection.execute(
            """
            SELECT batch_sha256, created_count, deduplicated_count
            FROM discovery_runs WHERE batch_id = %s FOR UPDATE
            """,
            (batch.batch_id,),
        ).fetchone()
        assert row is not None
        if row["batch_sha256"] != digest:
            raise DuplicateDiscoveryBatch(
                f"discovery batch {batch.batch_id} already contains different content"
            )
        frontier_ids: list[UUID] = []
        for candidate in batch.records:
            observation = connection.execute(
                """
                SELECT frontier_id FROM discovery_observations
                WHERE batch_id = %s AND candidate_identity_key = %s
                """,
                (batch.batch_id, candidate.identity_key),
            ).fetchone()
            if observation is None:
                raise DuplicateDiscoveryBatch("stored discovery batch is incomplete")
            frontier_ids.append(observation["frontier_id"])
        return DiscoveryIngestionResult(
            batch_id=batch.batch_id,
            created_count=row["created_count"],
            deduplicated_count=row["deduplicated_count"],
            records=tuple(self._get_record(connection, item) for item in frontier_ids),
        )

    def _store_evidence(
        self,
        connection: psycopg.Connection[dict[str, Any]],
        batch: DiscoveryBatch,
    ) -> None:
        for sequence, evidence in enumerate(batch.evidence, start=1):
            artifact = connection.execute(
                """
                SELECT artifact.artifact_id, artifact.visibility,
                    artifact.acquisition_method, blob.sha256, blob.media_type, blob.byte_size
                FROM artifacts AS artifact
                JOIN artifact_blobs AS blob ON blob.sha256 = artifact.blob_sha256
                WHERE artifact.artifact_id = %s
                """,
                (evidence.response_artifact.artifact_id,),
            ).fetchone()
            if artifact is None:
                raise InvalidDiscoveryRecord(
                    f"response artifact {evidence.response_artifact.artifact_id} does not exist"
                )
            if (
                artifact["visibility"] != "public"
                or artifact["acquisition_method"] != "source_adapter"
            ):
                raise InvalidDiscoveryRecord(
                    "discovery response evidence must be a public source-adapter artifact"
                )
            reference = evidence.response_artifact
            if (
                artifact["sha256"] != reference.sha256
                or artifact["media_type"] != reference.media_type
                or artifact["byte_size"] != reference.byte_size
            ):
                raise InvalidDiscoveryRecord(
                    "response artifact reference metadata does not match storage"
                )
            connection.execute(
                """
                INSERT INTO discovery_run_evidence (
                    batch_id, sequence, artifact_id, request_url, response_sha256,
                    response_media_type, http_status, fetched_at
                ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s)
                """,
                (
                    batch.batch_id,
                    sequence,
                    reference.artifact_id,
                    evidence.request_url,
                    evidence.response_sha256,
                    evidence.response_media_type,
                    evidence.http_status,
                    evidence.fetched_at,
                ),
            )

    def _upsert_candidate(
        self,
        connection: psycopg.Connection[dict[str, Any]],
        batch: DiscoveryBatch,
        candidate: DiscoveryRecord,
    ) -> tuple[UUID, bool]:
        matches = connection.execute(
            """
            SELECT frontier.*
            FROM crawl_frontier AS frontier
            WHERE frontier.frontier_id IN (
                SELECT candidate_frontier.frontier_id
                FROM crawl_frontier AS candidate_frontier
                LEFT JOIN crawl_frontier_aliases AS alias
                    ON alias.frontier_id = candidate_frontier.frontier_id
                WHERE candidate_frontier.identity_key = %s
                    OR alias.identity_key = %s
                    OR candidate_frontier.canonical_url = %s
            )
            FOR UPDATE OF frontier
            """,
            (candidate.identity_key, candidate.identity_key, candidate.canonical_url),
        ).fetchall()
        matched_ids = {row["frontier_id"] for row in matches}
        if len(matched_ids) > 1:
            raise InvalidDiscoveryRecord(
                "discovery identity and canonical URL resolve to different frontier records"
            )
        created = not matches
        if created:
            frontier_id = uuid4()
            status = _initial_status(candidate)
            connection.execute(
                """
                INSERT INTO crawl_frontier (
                    frontier_id, identity_key, primary_source_name, primary_source_version,
                    source_record_id, accession, entity_kind, canonical_url,
                    acquisition_method, status, priority, source_updated_at, discovered_at,
                    last_discovered_at, next_eligible_fetch_at, robots_status,
                    expected_media_types, license_json, access_status, access_reason,
                    trace_id, last_discovery_batch_id, version, created_at, updated_at
                ) VALUES (
                    %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s,
                    %s, %s, %s, %s, %s, %s, %s, %s, %s, 1, %s, %s
                )
                """,
                (
                    frontier_id,
                    candidate.identity_key,
                    batch.source.name,
                    batch.source.version,
                    candidate.source_record_id,
                    candidate.accession,
                    candidate.entity_kind.value,
                    candidate.canonical_url,
                    candidate.acquisition_method.value,
                    status.value,
                    candidate.priority,
                    candidate.source_updated_at,
                    candidate.discovered_at,
                    candidate.discovered_at,
                    candidate.next_eligible_fetch_at,
                    candidate.robots_status.value,
                    list(candidate.expected_media_types),
                    Jsonb(candidate.license.model_dump(mode="json")),
                    candidate.access_status.value,
                    candidate.access_reason,
                    candidate.trace_id,
                    batch.batch_id,
                    candidate.discovered_at,
                    candidate.discovered_at,
                ),
            )
        else:
            current = matches[0]
            if DiscoveryEntityKind(current["entity_kind"]) is not candidate.entity_kind:
                raise InvalidDiscoveryRecord(
                    "a canonical URL cannot represent different discovery entity kinds"
                )
            frontier_id = current["frontier_id"]
            self._rediscover(connection, current, batch, candidate)

        alias = connection.execute(
            """
            INSERT INTO crawl_frontier_aliases (identity_key, frontier_id)
            VALUES (%s, %s)
            ON CONFLICT (identity_key) DO UPDATE
                SET identity_key = EXCLUDED.identity_key
            RETURNING frontier_id
            """,
            (candidate.identity_key, frontier_id),
        ).fetchone()
        assert alias is not None
        if alias["frontier_id"] != frontier_id:
            raise InvalidDiscoveryRecord("discovery alias already names another frontier record")
        connection.execute(
            """
            INSERT INTO discovery_observations (
                batch_id, frontier_id, candidate_identity_key, candidate_json, observed_at
            ) VALUES (%s, %s, %s, %s, %s)
            """,
            (
                batch.batch_id,
                frontier_id,
                candidate.identity_key,
                Jsonb(candidate.model_dump(mode="json")),
                candidate.discovered_at,
            ),
        )
        self._event(
            connection,
            frontier_id,
            "discovered" if created else "rediscovered",
            candidate.trace_id,
            {"batch_id": str(batch.batch_id), "identity_key": candidate.identity_key},
            candidate.discovered_at,
        )
        return frontier_id, created

    def _rediscover(
        self,
        connection: psycopg.Connection[dict[str, Any]],
        current: dict[str, Any],
        batch: DiscoveryBatch,
        candidate: DiscoveryRecord,
    ) -> None:
        current_status = CrawlFrontierStatus(current["status"])
        current_access = DiscoveryAccessStatus(current["access_status"])
        candidate_prohibited = candidate.access_status is DiscoveryAccessStatus.PROHIBITED
        preserve_prohibition = current_access is DiscoveryAccessStatus.PROHIBITED
        access_status = (
            DiscoveryAccessStatus.PROHIBITED
            if candidate_prohibited or preserve_prohibition
            else candidate.access_status
        )
        access_reason = (
            candidate.access_reason
            if candidate_prohibited
            else current["access_reason"]
            if preserve_prohibition
            else candidate.access_reason
        )
        media_types = tuple(
            dict.fromkeys((*current["expected_media_types"], *candidate.expected_media_types))
        )
        if access_status is DiscoveryAccessStatus.PROHIBITED:
            status = CrawlFrontierStatus.PROHIBITED
        elif (
            current_status is CrawlFrontierStatus.UNSUPPORTED
            and media_types
            and candidate.access_status is DiscoveryAccessStatus.ALLOWED
        ):
            status = CrawlFrontierStatus.PENDING
        else:
            status = current_status
        current_license = _license(current["license_json"])
        license_record = (
            current_license
            if current_license.status is DiscoveryLicenseStatus.VERIFIED
            and candidate.license.status is not DiscoveryLicenseStatus.VERIFIED
            else candidate.license
        )
        robots_status = DiscoveryRobotsStatus(current["robots_status"])
        if candidate.robots_status is DiscoveryRobotsStatus.PROHIBITED:
            robots_status = DiscoveryRobotsStatus.PROHIBITED
        elif robots_status is not DiscoveryRobotsStatus.PROHIBITED:
            robots_status = candidate.robots_status
        source_updated_at = _latest_datetime(
            current["source_updated_at"], candidate.source_updated_at
        )
        connection.execute(
            """
            UPDATE crawl_frontier
            SET status = %s,
                source_updated_at = %s,
                last_discovered_at = GREATEST(last_discovered_at, %s),
                robots_status = %s,
                expected_media_types = %s,
                license_json = %s,
                access_status = %s,
                access_reason = %s,
                trace_id = %s,
                last_discovery_batch_id = %s,
                version = version + 1,
                updated_at = GREATEST(updated_at, %s)
            WHERE frontier_id = %s
            """,
            (
                status.value,
                source_updated_at,
                candidate.discovered_at,
                robots_status.value,
                list(media_types),
                Jsonb(license_record.model_dump(mode="json")),
                access_status.value,
                access_reason,
                candidate.trace_id,
                batch.batch_id,
                candidate.discovered_at,
                current["frontier_id"],
            ),
        )

    def _store_relationships(
        self,
        connection: psycopg.Connection[dict[str, Any]],
        candidate: DiscoveryRecord,
        *,
        frontier_id: UUID,
        created_at: datetime,
    ) -> None:
        for relationship in candidate.relationships:
            related = connection.execute(
                """
                SELECT frontier_id FROM crawl_frontier_aliases
                WHERE identity_key = %s
                """,
                (relationship.related_identity_key,),
            ).fetchone()
            if related is None:
                raise InvalidDiscoveryRecord(
                    f"related discovery identity {relationship.related_identity_key!r} is unknown"
                )
            if related["frontier_id"] == frontier_id:
                raise InvalidDiscoveryRecord("a frontier record cannot relate to itself")
            connection.execute(
                """
                INSERT INTO crawl_frontier_relationships (
                    frontier_id, related_frontier_id, relationship_kind, created_at
                ) VALUES (%s, %s, %s, %s)
                ON CONFLICT DO NOTHING
                """,
                (
                    frontier_id,
                    related["frontier_id"],
                    relationship.kind.value,
                    created_at,
                ),
            )

    def _locked(
        self,
        connection: psycopg.Connection[dict[str, Any]],
        frontier_id: UUID,
    ) -> dict[str, Any]:
        row = connection.execute(
            "SELECT * FROM crawl_frontier WHERE frontier_id = %s FOR UPDATE",
            (frontier_id,),
        ).fetchone()
        if row is None:
            raise FrontierNotFound(f"frontier record {frontier_id} does not exist")
        return row

    def _require_version(self, row: dict[str, Any], expected_version: int) -> None:
        if row["version"] != expected_version:
            raise FrontierConcurrencyConflict(
                f"frontier record changed from version {expected_version} to {row['version']}"
            )

    def _get_record(
        self,
        connection: psycopg.Connection[dict[str, Any]],
        frontier_id: UUID,
    ) -> CrawlFrontierRecord:
        row = connection.execute(
            FRONTIER_SELECT + " WHERE frontier.frontier_id = %s",
            (frontier_id,),
        ).fetchone()
        if row is None:
            raise FrontierNotFound(f"frontier record {frontier_id} does not exist")
        return _frontier_record(row)

    def _event(
        self,
        connection: psycopg.Connection[dict[str, Any]],
        frontier_id: UUID,
        event_type: str,
        trace_id: UUID | None,
        details: dict[str, object],
        created_at: datetime,
    ) -> None:
        connection.execute(
            """
            INSERT INTO crawl_frontier_events (
                frontier_id, event_type, trace_id, details, created_at
            ) VALUES (%s, %s, %s, %s, %s)
            """,
            (frontier_id, event_type, trace_id, Jsonb(details), created_at),
        )


def _initial_status(candidate: DiscoveryRecord) -> CrawlFrontierStatus:
    if candidate.access_status is DiscoveryAccessStatus.PROHIBITED:
        return CrawlFrontierStatus.PROHIBITED
    if candidate.access_status is not DiscoveryAccessStatus.ALLOWED:
        return CrawlFrontierStatus.UNSUPPORTED
    if not candidate.expected_media_types:
        return CrawlFrontierStatus.UNSUPPORTED
    return CrawlFrontierStatus.PENDING


def _frontier_record(row: dict[str, Any]) -> CrawlFrontierRecord:
    lease = None
    if row["lease_token"] is not None:
        lease = CrawlFrontierLease(
            owner=row["lease_owner"],
            token=row["lease_token"],
            expires_at=row["lease_expires_at"],
        )
    return CrawlFrontierRecord(
        frontier_id=row["frontier_id"],
        identity_key=row["identity_key"],
        primary_source=ToolIdentifier(
            name=row["primary_source_name"],
            version=row["primary_source_version"],
        ),
        source_record_id=row["source_record_id"],
        accession=row["accession"],
        entity_kind=DiscoveryEntityKind(row["entity_kind"]),
        canonical_url=row["canonical_url"],
        acquisition_method=DiscoveryAcquisitionMethod(row["acquisition_method"]),
        status=CrawlFrontierStatus(row["status"]),
        priority=row["priority"],
        source_updated_at=row["source_updated_at"],
        discovered_at=row["discovered_at"],
        last_discovered_at=row["last_discovered_at"],
        next_eligible_fetch_at=row["next_eligible_fetch_at"],
        attempt_count=row["attempt_count"],
        lease=lease,
        robots_status=DiscoveryRobotsStatus(row["robots_status"]),
        expected_media_types=tuple(row["expected_media_types"]),
        license=_license(row["license_json"]),
        access_status=DiscoveryAccessStatus(row["access_status"]),
        access_reason=row["access_reason"],
        trace_id=row["trace_id"],
        last_discovery_batch_id=row["last_discovery_batch_id"],
        relationships=_json_contract(_RELATIONSHIPS_ADAPTER, row["relationships_json"]),
        acquisitions=_json_contract(_ACQUISITIONS_ADAPTER, row["acquisitions_json"]),
        version=row["version"],
        created_at=row["created_at"],
        updated_at=row["updated_at"],
    )


def _license(value: object) -> DiscoveryLicense:
    return _json_contract(_LICENSE_ADAPTER, value)


def _json_contract(adapter: TypeAdapter[Any], value: object) -> Any:
    return adapter.validate_json(json.dumps(value), strict=True)


def _latest_datetime(first: datetime | None, second: datetime | None) -> datetime | None:
    if first is None:
        return second
    if second is None:
        return first
    return max(first, second)
