from uuid import uuid4

import httpx
import pytest

from apps.api.main import app
from hiveblot import api


@pytest.mark.asyncio
async def test_api_and_review_surfaces_are_wired() -> None:
    assert {"/api/records", "/api/v1/evidence-search", "/api/v1/evaluation-cases"} <= set(
        app.openapi()["paths"]
    )
    case_id = uuid4()
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        for path in (
            "/review",
            f"/workbench/{case_id}",
            f"/annotate/{case_id}",
            f"/spatial/{case_id}",
            f"/densitometry/{case_id}",
            "/metrics",
            "/review/assets/review.js",
        ):
            response = await client.get(path)
            assert response.status_code == 200, path


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
