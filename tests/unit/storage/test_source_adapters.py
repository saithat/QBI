import httpx
import pytest
from hiveblot_storage.errors import InvalidArtifact, SourceAccessDenied
from hiveblot_storage.source_adapters import HttpSourceAdapter


def test_http_source_adapter_streams_an_allowlisted_source() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.host == "repository.example"
        return httpx.Response(
            200,
            headers={"content-type": "application/pdf"},
            content=b"%PDF-1.7\nsource",
        )

    adapter = HttpSourceAdapter(
        allowed_hosts=("repository.example",),
        max_bytes=1000,
        client=httpx.Client(transport=httpx.MockTransport(handler)),
    )

    with adapter.fetch("https://repository.example/files/paper.bin") as payload:
        assert payload.stream.read() == b"%PDF-1.7\nsource"
        assert payload.original_filename == "paper.bin"
        assert payload.declared_media_type == "application/pdf"


def test_http_source_adapter_revalidates_redirect_hosts() -> None:
    def handler(_request: httpx.Request) -> httpx.Response:
        return httpx.Response(302, headers={"location": "http://private.internal/secret.pdf"})

    adapter = HttpSourceAdapter(
        allowed_hosts=("repository.example",),
        max_bytes=1000,
        client=httpx.Client(transport=httpx.MockTransport(handler)),
    )

    with (
        pytest.raises(SourceAccessDenied, match="not allowlisted"),
        adapter.fetch("https://repository.example/redirect"),
    ):
        pass


def test_http_source_adapter_enforces_streamed_size_limit() -> None:
    adapter = HttpSourceAdapter(
        allowed_hosts=("repository.example",),
        max_bytes=4,
        client=httpx.Client(
            transport=httpx.MockTransport(
                lambda _request: httpx.Response(200, content=b"too many bytes")
            )
        ),
    )

    with (
        pytest.raises(InvalidArtifact, match="size limit"),
        adapter.fetch("https://repository.example/file.pdf"),
    ):
        pass
