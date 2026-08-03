"""Bounded HTTP fetching with redirects, robots, conditional requests, and Retry-After."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from email.utils import parsedate_to_datetime
from typing import Protocol
from urllib.parse import urljoin, urlsplit, urlunsplit
from urllib.robotparser import RobotFileParser

import httpx
from hiveblot_contracts import CrawlFetchLease, DiscoveryAcquisitionMethod
from hiveblot_storage.media import normalize_media_type

from .fetch_service import FetchedPayload, FetchFailure
from .normalization import normalize_source_url
from .rate_limit import DomainRateLimitTimeout, SharedDomainRateLimiter


@dataclass(frozen=True, slots=True)
class RobotsCacheEntry:
    origin: str
    domain: str
    http_status: int
    rules_text: str | None
    fetched_at: datetime
    expires_at: datetime
    etag: str | None = None
    last_modified: str | None = None


class RobotsCache(Protocol):
    def get_robots(self, origin: str, *, at: datetime) -> RobotsCacheEntry | None: ...

    def store_robots(self, entry: RobotsCacheEntry) -> None: ...


@dataclass(frozen=True, slots=True)
class _HttpResponse:
    request_url: str
    final_url: str
    status: int
    headers: httpx.Headers
    content: bytes
    started_at: datetime
    completed_at: datetime


class HttpFetchClient:
    def __init__(
        self,
        *,
        rate_limiter: SharedDomainRateLimiter,
        robots_cache: RobotsCache,
        user_agent: str,
        allowed_hosts: tuple[str, ...],
        max_response_bytes: int,
        timeout_seconds: float = 30,
        max_redirects: int = 5,
        robots_cache_seconds: int = 86_400,
        robots_max_bytes: int = 524_288,
        client: httpx.Client | None = None,
        clock: Callable[[], datetime] | None = None,
    ) -> None:
        if not user_agent.strip():
            raise ValueError("fetch workers require an identifying user agent")
        if not allowed_hosts:
            raise ValueError("fetch workers require an explicit source-host allowlist")
        if max_response_bytes < 1 or robots_max_bytes < 1:
            raise ValueError("fetch response limits must be positive")
        if not 0 <= max_redirects <= 20:
            raise ValueError("max_redirects must be between 0 and 20")
        self._rate_limiter = rate_limiter
        self._robots_cache = robots_cache
        self._user_agent = user_agent
        self._allowed_hosts = frozenset(host.casefold() for host in allowed_hosts)
        self._max_response_bytes = max_response_bytes
        self._max_redirects = max_redirects
        self._robots_cache_seconds = robots_cache_seconds
        self._robots_max_bytes = robots_max_bytes
        self._client = client or httpx.Client(timeout=timeout_seconds, follow_redirects=False)
        self._clock = clock or (lambda: datetime.now(UTC))

    def fetch(self, lease: CrawlFetchLease) -> FetchedPayload:
        headers = {
            "User-Agent": self._user_agent,
            "Accept": ", ".join(lease.expected_media_types),
        }
        if lease.etag is not None:
            headers["If-None-Match"] = lease.etag
        if lease.last_modified is not None:
            headers["If-Modified-Since"] = lease.last_modified
        response = self._request_with_redirects(
            lease.canonical_url,
            worker_id=lease.worker_id,
            headers=headers,
            maximum_bytes=self._max_response_bytes,
            controlled=lease.acquisition_method is DiscoveryAcquisitionMethod.CONTROLLED_CRAWL,
        )
        if response.status == 304:
            return FetchedPayload(
                request_url=response.request_url,
                final_url=response.final_url,
                http_status=response.status,
                media_type=None,
                content=b"",
                etag=_bounded_header(response.headers, "etag"),
                last_modified=_bounded_header(response.headers, "last-modified"),
                started_at=response.started_at,
                completed_at=response.completed_at,
            )
        self._raise_for_status(response)
        media_type = normalize_media_type(response.headers.get("content-type"))
        return FetchedPayload(
            request_url=response.request_url,
            final_url=response.final_url,
            http_status=response.status,
            media_type=media_type,
            content=response.content,
            etag=_bounded_header(response.headers, "etag"),
            last_modified=_bounded_header(response.headers, "last-modified"),
            started_at=response.started_at,
            completed_at=response.completed_at,
        )

    def close(self) -> None:
        self._client.close()

    def _request_with_redirects(
        self,
        url: str,
        *,
        worker_id: str,
        headers: dict[str, str],
        maximum_bytes: int,
        controlled: bool,
    ) -> _HttpResponse:
        initial_url = normalize_source_url(url)
        current_url = initial_url
        initial_started_at: datetime | None = None
        for redirect_count in range(self._max_redirects + 1):
            if controlled and not self._robots_allowed(current_url, worker_id=worker_id):
                now = self._clock()
                raise FetchFailure(
                    "robots_prohibited",
                    f"robots policy prohibits {current_url}",
                    retryable=False,
                    prohibited=True,
                    final_url=current_url,
                    started_at=initial_started_at or now,
                    completed_at=now,
                )
            response = self._request_once(
                current_url,
                worker_id=worker_id,
                headers=headers,
                maximum_bytes=maximum_bytes,
            )
            initial_started_at = initial_started_at or response.started_at
            if response.status not in {301, 302, 303, 307, 308}:
                return _HttpResponse(
                    request_url=initial_url,
                    final_url=response.final_url,
                    status=response.status,
                    headers=response.headers,
                    content=response.content,
                    started_at=initial_started_at,
                    completed_at=response.completed_at,
                )
            location = response.headers.get("location")
            if not location:
                raise FetchFailure(
                    "invalid_redirect",
                    "redirect response omitted Location",
                    retryable=False,
                    final_url=current_url,
                    http_status=response.status,
                    started_at=initial_started_at,
                    completed_at=response.completed_at,
                )
            if redirect_count >= self._max_redirects:
                raise FetchFailure(
                    "redirect_limit",
                    "source exceeded the configured redirect limit",
                    retryable=False,
                    final_url=current_url,
                    http_status=response.status,
                    started_at=initial_started_at,
                    completed_at=response.completed_at,
                )
            current_url = normalize_source_url(urljoin(current_url, location))
        raise AssertionError("redirect loop must return or fail")

    def _request_once(
        self,
        url: str,
        *,
        worker_id: str,
        headers: dict[str, str],
        maximum_bytes: int,
    ) -> _HttpResponse:
        domain = _domain(url)
        started_at = self._clock()
        if domain not in self._allowed_hosts:
            completed_at = self._clock()
            raise FetchFailure(
                "source_host_not_allowed",
                f"source host {domain!r} is not allowlisted",
                retryable=False,
                prohibited=True,
                final_url=url,
                started_at=started_at,
                completed_at=completed_at,
            )
        try:
            permit = self._rate_limiter.acquire(domain, worker_id=worker_id)
        except DomainRateLimitTimeout as exc:
            completed_at = self._clock()
            raise FetchFailure(
                "domain_rate_limit_timeout",
                str(exc),
                retryable=True,
                final_url=url,
                started_at=started_at,
                completed_at=completed_at,
            ) from exc
        try:
            try:
                with self._client.stream("GET", url, headers=headers) as response:
                    declared_length = response.headers.get("content-length")
                    if declared_length is not None:
                        try:
                            length = int(declared_length)
                            if length < 0:
                                raise ValueError
                        except ValueError as exc:
                            raise FetchFailure(
                                "invalid_content_length",
                                "source returned an invalid Content-Length",
                                retryable=False,
                                final_url=url,
                                http_status=response.status_code,
                                started_at=started_at,
                                completed_at=self._clock(),
                            ) from exc
                        if length > maximum_bytes:
                            raise FetchFailure(
                                "content_too_large",
                                "source response exceeded the configured size limit",
                                retryable=False,
                                final_url=url,
                                http_status=response.status_code,
                                started_at=started_at,
                                completed_at=self._clock(),
                            )
                    chunks: list[bytes] = []
                    total = 0
                    for chunk in response.iter_bytes():
                        total += len(chunk)
                        if total > maximum_bytes:
                            raise FetchFailure(
                                "content_too_large",
                                "source response exceeded the configured size limit",
                                retryable=False,
                                final_url=url,
                                http_status=response.status_code,
                                started_at=started_at,
                                completed_at=self._clock(),
                            )
                        chunks.append(chunk)
                    completed_at = self._clock()
                    return _HttpResponse(
                        request_url=url,
                        final_url=normalize_source_url(str(response.request.url)),
                        status=response.status_code,
                        headers=response.headers,
                        content=b"".join(chunks),
                        started_at=started_at,
                        completed_at=completed_at,
                    )
            except FetchFailure:
                raise
            except httpx.HTTPError as exc:
                completed_at = self._clock()
                raise FetchFailure(
                    "http_transport_error",
                    "source request failed",
                    retryable=True,
                    final_url=url,
                    started_at=started_at,
                    completed_at=completed_at,
                ) from exc
        finally:
            self._rate_limiter.release(permit)

    def _robots_allowed(self, url: str, *, worker_id: str) -> bool:
        origin = _origin(url)
        now = self._clock()
        cached = self._robots_cache.get_robots(origin, at=now)
        if cached is None:
            robots_url = origin + "/robots.txt"
            headers = {"User-Agent": self._user_agent, "Accept": "text/plain"}
            response = self._request_with_redirects(
                robots_url,
                worker_id=worker_id,
                headers=headers,
                maximum_bytes=self._robots_max_bytes,
                controlled=False,
            )
            rules_text: str | None
            if response.status == 200:
                rules_text = response.content.decode("utf-8", errors="replace")
            elif response.status in {401, 403}:
                rules_text = "User-agent: *\nDisallow: /\n"
            elif response.status in {404, 410}:
                rules_text = ""
            elif response.status == 429 or response.status >= 500:
                self._raise_for_status(response)
                raise AssertionError("retryable robots response must raise")
            else:
                rules_text = ""
            cached = RobotsCacheEntry(
                origin=origin,
                domain=_domain(url),
                http_status=response.status,
                rules_text=rules_text,
                fetched_at=response.completed_at,
                expires_at=response.completed_at + timedelta(seconds=self._robots_cache_seconds),
                etag=_bounded_header(response.headers, "etag"),
                last_modified=_bounded_header(response.headers, "last-modified"),
            )
            self._robots_cache.store_robots(cached)
        parser = RobotFileParser()
        parser.set_url(origin + "/robots.txt")
        parser.parse((cached.rules_text or "").splitlines())
        return parser.can_fetch(self._user_agent, url)

    def _raise_for_status(self, response: _HttpResponse) -> None:
        if 200 <= response.status < 300:
            return
        retry_after = parse_retry_after(
            response.headers.get("retry-after"),
            now=response.completed_at,
        )
        retryable = response.status in {408, 425, 429} or response.status >= 500
        prohibited = response.status in {401, 403}
        raise FetchFailure(
            f"http_{response.status}",
            f"source returned HTTP {response.status}",
            retryable=retryable,
            prohibited=prohibited,
            final_url=response.final_url,
            http_status=response.status,
            retry_after=retry_after,
            started_at=response.started_at,
            completed_at=response.completed_at,
        )


def parse_retry_after(value: str | None, *, now: datetime) -> datetime | None:
    if value is None:
        return None
    candidate = value.strip()
    if not candidate:
        return None
    try:
        seconds = int(candidate)
    except ValueError:
        try:
            parsed = parsedate_to_datetime(candidate)
        except (TypeError, ValueError):
            return None
        if parsed.tzinfo is None:
            parsed = parsed.replace(tzinfo=UTC)
        return max(now, parsed.astimezone(UTC))
    return now + timedelta(seconds=max(0, seconds))


def _domain(url: str) -> str:
    parsed = urlsplit(url)
    if parsed.hostname is None:
        raise ValueError("fetch URL requires a host")
    return parsed.hostname.encode("idna").decode("ascii").casefold()


def _origin(url: str) -> str:
    parsed = urlsplit(url)
    domain = _domain(url)
    port = parsed.port
    default_port = (parsed.scheme == "http" and port == 80) or (
        parsed.scheme == "https" and port == 443
    )
    authority = domain if port is None or default_port else f"{domain}:{port}"
    return urlunsplit((parsed.scheme.casefold(), authority, "", "", ""))


def _bounded_header(headers: httpx.Headers, name: str) -> str | None:
    value = headers.get(name)
    if value is None:
        return None
    stripped = value.strip()
    return stripped[:1000] or None
