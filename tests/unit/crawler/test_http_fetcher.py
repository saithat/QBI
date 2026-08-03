from __future__ import annotations

from datetime import UTC, datetime, timedelta
from uuid import UUID, uuid4

import httpx
import pytest
from hiveblot_contracts import CrawlFetchLease, DiscoveryAcquisitionMethod
from hiveblot_crawler import (
    FetchFailure,
    HttpFetchClient,
    SharedDomainRateLimiter,
    parse_retry_after,
)

from tests.fakes.fetching import InMemoryDomainPermitRepository, InMemoryRobotsCache

NOW = datetime(2026, 8, 2, 12, tzinfo=UTC)


def _lease(
    *,
    acquisition_method: DiscoveryAcquisitionMethod = DiscoveryAcquisitionMethod.OFFICIAL_API,
    etag: str | None = None,
) -> CrawlFetchLease:
    return CrawlFetchLease(
        task_id=uuid4(),
        frontier_id=uuid4(),
        attempt_id=uuid4(),
        attempt=1,
        worker_id="worker-1",
        lease_token=uuid4(),
        canonical_url="https://source.test/paper.pdf",
        domain="source.test",
        acquisition_method=acquisition_method,
        expected_media_types=("application/pdf",),
        trace_id=UUID("00000000-0000-0000-0000-000000000123"),
        etag=etag,
        leased_at=NOW,
        expires_at=NOW + timedelta(minutes=2),
    )


def _client(handler, *, cache: InMemoryRobotsCache | None = None) -> HttpFetchClient:
    repository = InMemoryDomainPermitRepository()
    return HttpFetchClient(
        rate_limiter=SharedDomainRateLimiter(repository),
        robots_cache=cache or InMemoryRobotsCache(),
        user_agent="HiveBlotTest/1.0 (+https://example.test/contact)",
        allowed_hosts=("source.test",),
        max_response_bytes=1024,
        client=httpx.Client(transport=httpx.MockTransport(handler)),
        clock=lambda: NOW,
    )


def test_conditional_request_and_not_modified_response_are_preserved() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.headers["if-none-match"] == '"paper-v1"'
        return httpx.Response(304, headers={"ETag": '"paper-v1"'})

    client = _client(handler)
    result = client.fetch(_lease(etag='"paper-v1"'))
    assert result.http_status == 304
    assert result.content == b""
    assert result.etag == '"paper-v1"'


def test_controlled_crawl_obeys_robots_before_fetching_content() -> None:
    paths: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        paths.append(request.url.path)
        if request.url.path == "/robots.txt":
            return httpx.Response(200, text="User-agent: *\nDisallow: /paper.pdf\n")
        return httpx.Response(200, headers={"Content-Type": "application/pdf"}, content=b"%PDF")

    client = _client(handler)
    with pytest.raises(FetchFailure, match="robots policy") as caught:
        client.fetch(_lease(acquisition_method=DiscoveryAcquisitionMethod.CONTROLLED_CRAWL))
    assert caught.value.prohibited is True
    assert paths == ["/robots.txt"]


def test_redirect_limit_and_decoded_content_limit_fail_explicitly() -> None:
    def redirects(_request: httpx.Request) -> httpx.Response:
        return httpx.Response(302, headers={"Location": "/again"})

    client = HttpFetchClient(
        rate_limiter=SharedDomainRateLimiter(InMemoryDomainPermitRepository()),
        robots_cache=InMemoryRobotsCache(),
        user_agent="HiveBlotTest/1.0 (+https://example.test/contact)",
        allowed_hosts=("source.test",),
        max_response_bytes=8,
        max_redirects=1,
        client=httpx.Client(transport=httpx.MockTransport(redirects)),
        clock=lambda: NOW,
    )
    with pytest.raises(FetchFailure) as redirected:
        client.fetch(_lease())
    assert redirected.value.error.code == "redirect_limit"

    def oversized(_request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            headers={"Content-Type": "application/pdf"},
            content=b"0123456789",
        )

    client = HttpFetchClient(
        rate_limiter=SharedDomainRateLimiter(InMemoryDomainPermitRepository()),
        robots_cache=InMemoryRobotsCache(),
        user_agent="HiveBlotTest/1.0 (+https://example.test/contact)",
        allowed_hosts=("source.test",),
        max_response_bytes=8,
        client=httpx.Client(transport=httpx.MockTransport(oversized)),
        clock=lambda: NOW,
    )
    with pytest.raises(FetchFailure) as too_large:
        client.fetch(_lease())
    assert too_large.value.error.code == "content_too_large"


def test_retry_after_supports_delta_seconds_and_http_dates() -> None:
    assert parse_retry_after("120", now=NOW) == NOW + timedelta(seconds=120)
    assert parse_retry_after("Sun, 02 Aug 2026 12:03:00 GMT", now=NOW) == NOW + timedelta(minutes=3)
    assert parse_retry_after("nonsense", now=NOW) is None


def test_redirects_cannot_escape_the_explicit_source_allowlist() -> None:
    requested: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requested.append(request.url.host)
        return httpx.Response(302, headers={"Location": "https://private.test/internal"})

    client = _client(handler)
    with pytest.raises(FetchFailure) as blocked:
        client.fetch(_lease())
    assert blocked.value.error.code == "source_host_not_allowed"
    assert requested == ["source.test"]
