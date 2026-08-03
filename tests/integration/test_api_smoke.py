from datetime import UTC, datetime

import httpx
import pytest
from pydantic import ValidationError

from apps.api.main import app
from apps.api.schemas import SearchRequest
from hiveblot import api


def test_api_entrypoint_serves_versioned_strict_responses(monkeypatch) -> None:
    monkeypatch.setattr(
        api.db,
        "list_records",
        lambda *_args, **_kwargs: [
            {
                "id": 1,
                "paper_id": "10.1234/example",
                "source_pdf": None,
                "candidate_path": None,
                "page": 2,
                "figure_label": "Figure 1",
                "panel_label": "A",
                "row_index": 1,
                "lane_index": 1,
                "target": "p53",
                "is_loading_control": False,
                "western_blot_type": "total_protein",
                "sample": "A549",
                "organism": "human",
                "treatment_context": "Nutlin-3",
                "condition": "10 uM",
                "band_state": "present",
                "confidence": 0.9,
                "updated_at": datetime(2026, 8, 2, tzinfo=UTC),
            }
        ],
    )
    api.get_settings.cache_clear()

    response = api.records(limit=1)
    openapi = app.openapi()

    assert response.schema_version == "1.0"
    assert response.results[0].target == "p53"
    assert "/api/records" in openapi["paths"]
    with pytest.raises(ValidationError, match="Extra inputs are not permitted"):
        SearchRequest.model_validate({"query": "p53", "unknown": "rejected"})


@pytest.mark.asyncio
async def test_health_endpoint_smoke_over_asgi(monkeypatch) -> None:
    monkeypatch.setattr(api.db, "health", lambda _: True)

    async def model_health(_self) -> bool:
        return True

    monkeypatch.setattr("apps.api.application.LocalModelClient.health", model_health)
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        response = await client.get("/health")

    assert response.status_code == 200
    assert response.json() == {
        "schema_version": "1.0",
        "status": "ok",
        "database": "ok",
        "model": "ok",
    }


@pytest.mark.asyncio
async def test_kubernetes_runtime_probes_separate_process_health_from_readiness(
    monkeypatch,
) -> None:
    monkeypatch.setattr(api.db, "health", lambda _: False)
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        live = await client.get("/health/live")
        startup = await client.get("/health/startup")
        not_ready = await client.get("/health/ready")
        monkeypatch.setattr(api.db, "health", lambda _: True)
        ready = await client.get("/health/ready")

    assert live.status_code == 200
    assert live.json() == {"schema_version": "1.0", "status": "alive"}
    assert startup.status_code == 200
    assert startup.json() == {"schema_version": "1.0", "status": "started"}
    assert not_ready.status_code == 503
    assert not_ready.json() == {"schema_version": "1.0", "status": "not_ready"}
    assert ready.status_code == 200
    assert ready.json() == {"schema_version": "1.0", "status": "ready"}
