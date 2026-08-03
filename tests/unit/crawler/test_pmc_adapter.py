from __future__ import annotations

from datetime import UTC, date, datetime
from urllib.parse import parse_qs
from uuid import UUID

import httpx
import pytest
from hiveblot_contracts import (
    DiscoveryAccessStatus,
    DiscoveryEntityKind,
    DiscoveryQuery,
    DiscoveryRelationshipKind,
)
from hiveblot_crawler import DiscoverySourceError, PmcOaiDiscoveryAdapter

from tests.fakes.discovery import InMemoryDiscoveryEvidencePublisher

NOW = datetime(2026, 8, 2, 12, tzinfo=UTC)


def _response(*headers: str, token: str | None = None) -> bytes:
    token_xml = f"<resumptionToken>{token}</resumptionToken>" if token else ""
    return (
        '<?xml version="1.0" encoding="UTF-8"?>'
        '<OAI-PMH xmlns="http://www.openarchives.org/OAI/2.0/">'
        "<ListIdentifiers>" + "".join(headers) + token_xml + "</ListIdentifiers></OAI-PMH>"
    ).encode()


def _header(identifier: int, *, deleted: bool = False) -> str:
    status = ' status="deleted"' if deleted else ""
    return (
        f"<header{status}>"
        f"<identifier>oai:pubmedcentral.nih.gov:{identifier}</identifier>"
        "<datestamp>2026-08-01</datestamp><setSpec>pmc-open</setSpec>"
        "</header>"
    )


def test_pmc_adapter_paginates_official_api_and_emits_linked_records() -> None:
    requests: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        query = parse_qs(request.url.query.decode())
        content = (
            _response(_header(123), token="next-page")
            if "resumptionToken" not in query
            else _response(_header(456, deleted=True))
        )
        return httpx.Response(
            200,
            headers={"content-type": "application/xml; charset=UTF-8"},
            content=content,
        )

    client = httpx.Client(transport=httpx.MockTransport(handler))
    publisher = InMemoryDiscoveryEvidencePublisher()
    adapter = PmcOaiDiscoveryAdapter(
        user_agent="HiveBlot test contact@example.test",
        evidence_publisher=publisher,
        client=client,
        clock=lambda: NOW,
    )
    batch = adapter.discover(
        DiscoveryQuery(
            from_date=date(2026, 8, 1),
            until_date=date(2026, 8, 2),
            maximum_pages=2,
        ),
        trace_id=UUID("00000000-0000-0000-0000-000000000123"),
    )

    assert len(requests) == 2
    first_query = parse_qs(requests[0].url.query.decode())
    assert first_query == {
        "verb": ["ListIdentifiers"],
        "metadataPrefix": ["pmc"],
        "set": ["pmc-open"],
        "from": ["2026-08-01"],
        "until": ["2026-08-02"],
    }
    assert parse_qs(requests[1].url.query.decode()) == {
        "verb": ["ListIdentifiers"],
        "resumptionToken": ["next-page"],
    }
    assert requests[0].headers["user-agent"] == "HiveBlot test contact@example.test"
    assert len(batch.evidence) == 2
    assert [item[2] for item in publisher.responses] == [
        _response(_header(123), token="next-page"),
        _response(_header(456, deleted=True)),
    ]
    assert batch.evidence[0].response_artifact == publisher.responses[0][3]
    assert batch.next_cursor is None
    assert {record.identity_key for record in batch.records} == {
        "pmc:pmc123",
        "pmc-metadata:pmc123",
        "pmc:pmc456",
    }
    metadata = next(
        item for item in batch.records if item.entity_kind is DiscoveryEntityKind.METADATA_RECORD
    )
    assert metadata.relationships[0].related_identity_key == "pmc:pmc123"
    assert metadata.relationships[0].kind is DiscoveryRelationshipKind.DESCRIBES
    deleted = next(item for item in batch.records if item.identity_key == "pmc:pmc456")
    assert deleted.access_status is DiscoveryAccessStatus.PROHIBITED
    assert deleted.access_reason == "source_record_deleted"
    assert deleted.expected_media_types == ()


def test_pmc_adapter_returns_cursor_when_page_budget_is_exhausted() -> None:
    client = httpx.Client(
        transport=httpx.MockTransport(
            lambda request: httpx.Response(
                200,
                headers={"content-type": "text/xml"},
                content=_response(_header(123), token="continue"),
            )
        )
    )
    batch = PmcOaiDiscoveryAdapter(
        user_agent="HiveBlot test contact@example.test",
        evidence_publisher=InMemoryDiscoveryEvidencePublisher(),
        client=client,
        clock=lambda: NOW,
    ).discover(DiscoveryQuery(maximum_pages=1))
    assert batch.next_cursor == "continue"


def test_pmc_adapter_stops_streaming_before_oversized_response_publication() -> None:
    publisher = InMemoryDiscoveryEvidencePublisher()
    client = httpx.Client(
        transport=httpx.MockTransport(
            lambda request: httpx.Response(
                200,
                headers={"content-type": "application/xml"},
                content=_response(_header(123)),
            )
        )
    )
    adapter = PmcOaiDiscoveryAdapter(
        user_agent="HiveBlot test contact@example.test",
        evidence_publisher=publisher,
        client=client,
        max_response_bytes=10,
        clock=lambda: NOW,
    )
    with pytest.raises(DiscoverySourceError, match="size limit"):
        adapter.discover(DiscoveryQuery(maximum_pages=1))
    assert publisher.responses == []


def test_pmc_adapter_translates_transport_errors() -> None:
    def fail(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("offline", request=request)

    adapter = PmcOaiDiscoveryAdapter(
        user_agent="HiveBlot test contact@example.test",
        evidence_publisher=InMemoryDiscoveryEvidencePublisher(),
        client=httpx.Client(transport=httpx.MockTransport(fail)),
        clock=lambda: NOW,
    )
    with pytest.raises(DiscoverySourceError, match="request failed"):
        adapter.discover(DiscoveryQuery(maximum_pages=1))


@pytest.mark.parametrize(
    ("status", "media_type", "content"),
    [
        (429, "application/xml", _response()),
        (200, "text/html", b"<html></html>"),
        (200, "application/xml", b"<!DOCTYPE root><root/>"),
    ],
)
def test_pmc_adapter_fails_closed_on_unusable_source_responses(
    status: int,
    media_type: str,
    content: bytes,
) -> None:
    client = httpx.Client(
        transport=httpx.MockTransport(
            lambda request: httpx.Response(
                status,
                headers={"content-type": media_type},
                content=content,
            )
        )
    )
    adapter = PmcOaiDiscoveryAdapter(
        user_agent="HiveBlot test contact@example.test",
        evidence_publisher=InMemoryDiscoveryEvidencePublisher(),
        client=client,
        clock=lambda: NOW,
    )
    with pytest.raises(DiscoverySourceError):
        adapter.discover(DiscoveryQuery(maximum_pages=1))
