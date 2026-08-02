from pathlib import Path

import httpx
import pytest

from hiveblot.model_client import LocalModelClient, ModelUnavailable, parse_filters_content
from hiveblot.settings import Settings


def settings() -> Settings:
    return Settings(
        database_url="postgresql://unused",
        vllm_base_url="http://model.test/v1",
        vllm_model="local-model",
        vllm_api_key="local",
        vllm_timeout_seconds=10,
        vllm_max_tokens=100,
        vllm_image_max_side=1000,
        data_dir=Path("/tmp"),
    )


def test_parse_filters_content_normalizes_empty_values() -> None:
    filters = parse_filters_content('{"target":" p53 ","sample":"","condition":null}')

    assert filters.model_dump() == {
        "schema_version": "1.0",
        "target": "p53",
        "sample": None,
        "condition": None,
    }


def test_parse_filters_content_rejects_unexpected_fields() -> None:
    with pytest.raises(ModelUnavailable):
        parse_filters_content('{"target":"p53","sample":null,"condition":null,"sql":"DROP TABLE"}')


@pytest.mark.asyncio
async def test_local_model_client_health_and_search() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/health":
            return httpx.Response(200)
        assert request.url.path == "/v1/chat/completions"
        assert request.headers["authorization"] == "Bearer local"
        payload = __import__("json").loads(request.content)
        assert payload["response_format"]["type"] == "json_schema"
        assert payload["chat_template_kwargs"] == {"enable_thinking": False}
        return httpx.Response(
            200,
            json={
                "choices": [
                    {
                        "message": {
                            "content": ('{"target":"p53","sample":"A549","condition":"Nutlin-3"}')
                        }
                    }
                ]
            },
        )

    client = LocalModelClient(settings(), transport=httpx.MockTransport(handler))

    assert await client.health() is True
    assert (await client.parse_search("p53 in A549 with Nutlin-3")).model_dump() == {
        "schema_version": "1.0",
        "target": "p53",
        "sample": "A549",
        "condition": "Nutlin-3",
    }


@pytest.mark.asyncio
async def test_local_model_client_wraps_http_failures() -> None:
    transport = httpx.MockTransport(lambda _: httpx.Response(500))
    client = LocalModelClient(settings(), transport=transport)

    assert await client.health() is False
    with pytest.raises(ModelUnavailable):
        await client.parse_search("p53")
