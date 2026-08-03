"""API-first adapters that normalize public source discovery responses."""

from __future__ import annotations

import hashlib
import re
import xml.etree.ElementTree as ET
from collections.abc import Callable, Mapping
from datetime import UTC, date, datetime
from typing import Protocol
from urllib.parse import urlencode
from uuid import UUID, uuid4

import httpx
from hiveblot_contracts import (
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

from .errors import DiscoverySourceError
from .evidence import DiscoveryEvidencePublisher
from .normalization import canonical_pmc_accession, normalize_source_url, pmc_identity_key

PMC_OAI_BASE_URL = "https://pmc.ncbi.nlm.nih.gov/api/oai/v1/mh/"
PMC_OAI_NAMESPACE = "http://www.openarchives.org/OAI/2.0/"
_PMC_OAI_IDENTIFIER = re.compile(r"^oai:pubmedcentral\.nih\.gov:([1-9][0-9]*)$")


class DiscoveryAdapter(Protocol):
    name: str
    version: str

    def discover(
        self,
        query: DiscoveryQuery,
        *,
        trace_id: UUID | None = None,
    ) -> DiscoveryBatch: ...


class PmcOaiDiscoveryAdapter:
    """Discover PMC open-set records through the official OAI-PMH API."""

    name = "pmc-oai"
    version = "1.0.0"

    def __init__(
        self,
        *,
        base_url: str = PMC_OAI_BASE_URL,
        user_agent: str,
        evidence_publisher: DiscoveryEvidencePublisher,
        timeout_seconds: float = 30,
        max_response_bytes: int = 5_000_000,
        client: httpx.Client | None = None,
        clock: Callable[[], datetime] | None = None,
    ) -> None:
        if not user_agent.strip():
            raise ValueError("PMC OAI discovery requires an identifying user agent")
        if max_response_bytes < 1:
            raise ValueError("maximum discovery response size must be positive")
        self._base_url = normalize_source_url(base_url)
        self._max_response_bytes = max_response_bytes
        self._evidence_publisher = evidence_publisher
        self._clock = clock or (lambda: datetime.now(UTC))
        self._headers = {
            "User-Agent": user_agent,
            "Accept": "application/xml",
            "Accept-Encoding": "gzip, deflate",
        }
        self._client = client or httpx.Client(
            timeout=timeout_seconds,
            follow_redirects=False,
        )

    def discover(
        self,
        query: DiscoveryQuery,
        *,
        trace_id: UUID | None = None,
    ) -> DiscoveryBatch:
        batch_id = uuid4()
        run_trace_id = trace_id or uuid4()
        started_at = self._clock()
        cursor = query.cursor
        records: dict[str, DiscoveryRecord] = {}
        evidence: list[DiscoveryEvidence] = []

        for _page in range(query.maximum_pages):
            parameters = self._parameters(query, cursor)
            status_code, request_url, content = _fetch_oai_page(
                self._client,
                base_url=self._base_url,
                parameters=parameters,
                headers=self._headers,
                maximum_bytes=self._max_response_bytes,
            )
            fetched_at = self._clock()
            normalized_media_type = "application/xml"
            response_artifact = self._evidence_publisher.publish_response(
                request_url=request_url,
                media_type=normalized_media_type,
                content=content,
            )
            evidence.append(
                DiscoveryEvidence(
                    request_url=request_url,
                    response_artifact=response_artifact,
                    response_sha256=hashlib.sha256(content).hexdigest(),
                    response_media_type=normalized_media_type,
                    http_status=status_code,
                    fetched_at=fetched_at,
                )
            )
            root = _parse_oai_xml(content)
            source_errors = root.findall(f"{{{PMC_OAI_NAMESPACE}}}error")
            if source_errors:
                codes = {item.attrib.get("code", "unknown") for item in source_errors}
                if codes == {"noRecordsMatch"}:
                    cursor = None
                    break
                raise DiscoverySourceError("PMC OAI error: " + ", ".join(sorted(codes)))

            listing = root.find(f"{{{PMC_OAI_NAMESPACE}}}ListIdentifiers")
            if listing is None:
                raise DiscoverySourceError("PMC OAI response omitted ListIdentifiers")
            for header in listing.findall(f"{{{PMC_OAI_NAMESPACE}}}header"):
                for record in self._records_from_header(
                    header,
                    discovered_at=fetched_at,
                    trace_id=run_trace_id,
                ):
                    existing = records.get(record.identity_key)
                    if existing is not None and existing != record:
                        raise DiscoverySourceError(
                            f"PMC OAI returned conflicting record {record.identity_key!r}"
                        )
                    records[record.identity_key] = record
            token = listing.find(f"{{{PMC_OAI_NAMESPACE}}}resumptionToken")
            cursor = token.text.strip() if token is not None and token.text else None
            if not cursor:
                break

        return DiscoveryBatch(
            batch_id=batch_id,
            source=ToolIdentifier(name=self.name, version=self.version),
            query=query,
            trace_id=run_trace_id,
            started_at=started_at,
            completed_at=self._clock(),
            records=tuple(records.values()),
            evidence=tuple(evidence),
            next_cursor=cursor,
        )

    def _parameters(self, query: DiscoveryQuery, cursor: str | None) -> Mapping[str, str]:
        if cursor is not None:
            return {"verb": "ListIdentifiers", "resumptionToken": cursor}
        parameters = {
            "verb": "ListIdentifiers",
            "metadataPrefix": "pmc",
            "set": "pmc-open",
        }
        if query.from_date is not None:
            parameters["from"] = query.from_date.isoformat()
        if query.until_date is not None:
            parameters["until"] = query.until_date.isoformat()
        return parameters

    def _records_from_header(
        self,
        header: ET.Element,
        *,
        discovered_at: datetime,
        trace_id: UUID,
    ) -> tuple[DiscoveryRecord, ...]:
        identifier = _required_text(header, "identifier")
        match = _PMC_OAI_IDENTIFIER.fullmatch(identifier)
        if match is None:
            raise DiscoverySourceError(f"unexpected PMC OAI identifier {identifier!r}")
        accession = canonical_pmc_accession(match.group(1))
        paper_key = pmc_identity_key(accession)
        source_updated_at = _oai_datestamp(_required_text(header, "datestamp"))
        deleted = header.attrib.get("status") == "deleted"
        access_status = (
            DiscoveryAccessStatus.PROHIBITED if deleted else DiscoveryAccessStatus.ALLOWED
        )
        reason = "source_record_deleted" if deleted else None
        license_record = DiscoveryLicense(
            status=DiscoveryLicenseStatus.UNVERIFIED,
            statement=("PMC open-set membership; verify the article-level license before reuse"),
            verification_required=True,
        )
        paper = DiscoveryRecord(
            identity_key=paper_key,
            source_record_id=identifier,
            accession=accession,
            entity_kind=DiscoveryEntityKind.PAPER,
            canonical_url=_get_record_url(self._base_url, identifier, "pmc"),
            acquisition_method=DiscoveryAcquisitionMethod.OFFICIAL_API,
            priority=100,
            source_updated_at=source_updated_at,
            discovered_at=discovered_at,
            next_eligible_fetch_at=discovered_at,
            robots_status=DiscoveryRobotsStatus.NOT_APPLICABLE,
            expected_media_types=() if deleted else ("application/xml",),
            license=license_record,
            access_status=access_status,
            access_reason=reason,
            trace_id=trace_id,
        )
        if deleted:
            return (paper,)
        metadata = DiscoveryRecord(
            identity_key=pmc_identity_key(accession, metadata=True),
            source_record_id=f"{identifier}:front-matter",
            accession=accession,
            entity_kind=DiscoveryEntityKind.METADATA_RECORD,
            canonical_url=_get_record_url(self._base_url, identifier, "pmc_fm"),
            acquisition_method=DiscoveryAcquisitionMethod.OFFICIAL_API,
            priority=90,
            source_updated_at=source_updated_at,
            discovered_at=discovered_at,
            next_eligible_fetch_at=discovered_at,
            robots_status=DiscoveryRobotsStatus.NOT_APPLICABLE,
            expected_media_types=("application/xml",),
            license=license_record,
            access_status=DiscoveryAccessStatus.ALLOWED,
            relationships=(
                DiscoveryRelationship(
                    related_identity_key=paper_key,
                    kind=DiscoveryRelationshipKind.DESCRIBES,
                ),
            ),
            trace_id=trace_id,
        )
        return paper, metadata

    def close(self) -> None:
        self._client.close()


def _parse_oai_xml(content: bytes) -> ET.Element:
    lowered = content[:4096].lower()
    if b"<!doctype" in lowered or b"<!entity" in lowered:
        raise DiscoverySourceError("PMC OAI response contained a prohibited XML declaration")
    try:
        return ET.fromstring(content)
    except ET.ParseError as exc:
        raise DiscoverySourceError("PMC OAI returned invalid XML") from exc


def _bounded_response_content(response: httpx.Response, *, maximum_bytes: int) -> bytes:
    declared_length = response.headers.get("content-length")
    if declared_length is not None:
        try:
            parsed_length = int(declared_length)
            if parsed_length < 0:
                raise ValueError
            if parsed_length > maximum_bytes:
                raise DiscoverySourceError("PMC OAI response exceeded the configured size limit")
        except ValueError as exc:
            raise DiscoverySourceError("PMC OAI returned an invalid Content-Length") from exc
    chunks: list[bytes] = []
    total = 0
    for chunk in response.iter_bytes():
        total += len(chunk)
        if total > maximum_bytes:
            raise DiscoverySourceError("PMC OAI response exceeded the configured size limit")
        chunks.append(chunk)
    return b"".join(chunks)


def _fetch_oai_page(
    client: httpx.Client,
    *,
    base_url: str,
    parameters: Mapping[str, str],
    headers: Mapping[str, str],
    maximum_bytes: int,
) -> tuple[int, str, bytes]:
    try:
        with client.stream(
            "GET",
            base_url,
            params=parameters,
            headers=headers,
        ) as response:
            if response.is_redirect:
                raise DiscoverySourceError("PMC OAI endpoint unexpectedly redirected")
            try:
                response.raise_for_status()
            except httpx.HTTPStatusError as exc:
                raise DiscoverySourceError(f"PMC OAI returned HTTP {response.status_code}") from exc
            media_type = (
                response.headers.get("content-type", "application/xml")
                .split(";", 1)[0]
                .strip()
                .casefold()
            )
            if media_type not in {"application/xml", "text/xml"}:
                raise DiscoverySourceError(
                    f"PMC OAI returned unsupported media type {media_type!r}"
                )
            content = _bounded_response_content(response, maximum_bytes=maximum_bytes)
            return (
                response.status_code,
                normalize_source_url(str(response.request.url)),
                content,
            )
    except DiscoverySourceError:
        raise
    except httpx.HTTPError as exc:
        raise DiscoverySourceError("PMC OAI request failed") from exc


def _required_text(parent: ET.Element, local_name: str) -> str:
    item = parent.find(f"{{{PMC_OAI_NAMESPACE}}}{local_name}")
    if item is None or not item.text or not item.text.strip():
        raise DiscoverySourceError(f"PMC OAI header omitted {local_name}")
    return item.text.strip()


def _oai_datestamp(value: str) -> datetime:
    try:
        if "T" not in value:
            return datetime.combine(date.fromisoformat(value), datetime.min.time(), tzinfo=UTC)
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError as exc:
        raise DiscoverySourceError(f"invalid PMC OAI datestamp {value!r}") from exc
    if parsed.tzinfo is None:
        raise DiscoverySourceError("PMC OAI datestamp must include a timezone")
    return parsed


def _get_record_url(base_url: str, identifier: str, metadata_prefix: str) -> str:
    query = urlencode(
        {
            "verb": "GetRecord",
            "identifier": identifier,
            "metadataPrefix": metadata_prefix,
        }
    )
    return normalize_source_url(f"{base_url}?{query}")
