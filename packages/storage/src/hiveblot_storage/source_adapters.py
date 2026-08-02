"""Server-side source adapters normalize external bytes before strict publication."""

from __future__ import annotations

import tempfile
from collections.abc import Iterator, Mapping
from contextlib import AbstractContextManager, contextmanager
from dataclasses import dataclass
from pathlib import PurePosixPath
from typing import BinaryIO, Protocol
from urllib.parse import unquote, urljoin, urlsplit

import httpx

from .errors import InvalidArtifact, SourceAccessDenied


@dataclass(frozen=True, slots=True)
class SourcePayload:
    stream: BinaryIO
    original_filename: str
    declared_media_type: str | None


class SourceAdapter(Protocol):
    def fetch(self, source_uri: str) -> AbstractContextManager[SourcePayload]: ...


class SourceAdapterRegistry:
    def __init__(self, adapters: Mapping[str, SourceAdapter]) -> None:
        self._adapters = dict(adapters)

    def get(self, name: str) -> SourceAdapter:
        try:
            return self._adapters[name]
        except KeyError as exc:
            raise SourceAccessDenied(f"source adapter {name!r} is not configured") from exc


class HttpSourceAdapter:
    """Allowlist-only HTTP adapter; arbitrary crawling belongs to PRD-015/016."""

    def __init__(
        self,
        *,
        allowed_hosts: tuple[str, ...],
        max_bytes: int,
        timeout_seconds: float = 60,
        max_redirects: int = 5,
        client: httpx.Client | None = None,
    ) -> None:
        self._allowed_hosts = frozenset(host.casefold() for host in allowed_hosts)
        self._max_bytes = max_bytes
        self._max_redirects = max_redirects
        self._client = client or httpx.Client(
            timeout=timeout_seconds,
            follow_redirects=False,
            headers={"User-Agent": "HiveBlot source-ingestion/1.0"},
        )

    def fetch(self, source_uri: str) -> AbstractContextManager[SourcePayload]:
        return self._fetch(source_uri)

    @contextmanager
    def _fetch(self, source_uri: str) -> Iterator[SourcePayload]:
        current = source_uri
        for redirect_count in range(self._max_redirects + 1):
            self._validate_uri(current)
            with self._client.stream("GET", current) as response:
                if response.is_redirect:
                    if redirect_count == self._max_redirects:
                        raise InvalidArtifact("source exceeded redirect limit")
                    location = response.headers.get("location")
                    if not location:
                        raise InvalidArtifact("source redirect omitted a location")
                    current = urljoin(current, location)
                    continue

                response.raise_for_status()
                content_length = response.headers.get("content-length")
                if content_length is not None and int(content_length) > self._max_bytes:
                    raise InvalidArtifact("source exceeds the configured artifact size limit")

                with tempfile.TemporaryFile("w+b") as stream:
                    byte_size = 0
                    for chunk in response.iter_bytes():
                        byte_size += len(chunk)
                        if byte_size > self._max_bytes:
                            raise InvalidArtifact(
                                "source exceeds the configured artifact size limit"
                            )
                        stream.write(chunk)
                    stream.seek(0)
                    yield SourcePayload(
                        stream=stream,
                        original_filename=_source_filename(current),
                        declared_media_type=response.headers.get("content-type"),
                    )
                    return
        raise InvalidArtifact("source exceeded redirect limit")

    def _validate_uri(self, source_uri: str) -> None:
        parsed = urlsplit(source_uri)
        host = parsed.hostname.casefold() if parsed.hostname else ""
        if parsed.scheme not in {"http", "https"} or not host or parsed.username is not None:
            raise SourceAccessDenied("source URI must be an unauthenticated HTTP(S) URL")
        if host not in self._allowed_hosts:
            raise SourceAccessDenied(f"source host {host!r} is not allowlisted")


def _source_filename(source_uri: str) -> str:
    name = unquote(PurePosixPath(urlsplit(source_uri).path).name).strip()
    return name[:1024] or "source-artifact"
