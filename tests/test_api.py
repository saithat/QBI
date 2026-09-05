import json
import re
from datetime import UTC, datetime
from types import SimpleNamespace

import pytest
from fastapi.routing import APIRoute
from fastapi.testclient import TestClient

from hiveblot import api
from hiveblot.api import _page_context, _safe_candidate_path


@pytest.fixture
def client(monkeypatch, tmp_path):
    settings = SimpleNamespace(database_url="test-database", data_dir=tmp_path / "data")
    monkeypatch.setattr(api, "get_settings", lambda: settings)
    monkeypatch.setattr(api.db, "initialize", lambda _: None)
    with TestClient(api.app) as client:
        yield client


def catalog_record():
    return {
        "id": 7,
        "paper_id": "doi:10.1000/example",
        "page": 5,
        "figure_label": "FIGURE 2",
        "panel_label": "A",
        "row_index": 1,
        "lane_index": 2,
        "target": "TP53",
        "is_loading_control": False,
        "western_blot_type": "western_blot",
        "sample": "A549",
        "organism": "human",
        "treatment_context": None,
        "condition": "Nutlin-3",
        "band_state": "present",
        "confidence": 0.9,
        "image_sha256": "a" * 64,
        "model_version": "model@revision",
        "source_url": "https://doi.org/10.1000/example",
        "updated_at": datetime(2026, 9, 5, tzinfo=UTC),
        "candidate_path": "/private/internal/image.png",
        "source_pdf": "/private/internal/paper.pdf",
    }


def test_public_catalog_filters_and_paginates_without_authentication(client, monkeypatch):
    calls = []

    def search(database_url, filters, *, limit, offset):
        calls.append((database_url, filters, limit, offset))
        return ([catalog_record()] if offset < 17 else []), 17

    monkeypatch.setattr(api.db, "search_records", search)
    response = client.get(
        "/api/records",
        params={
            "q": "TP53 Nutlin",
            "target": "TP53",
            "sample": "A549",
            "condition": "Nutlin",
            "paper_id": "doi:10.1000/example",
            "limit": 1,
            "offset": 2,
        },
    )
    assert response.status_code == 200
    body = response.json()
    assert (body["count"], body["total"], body["limit"], body["offset"]) == (1, 17, 1, 2)
    result = body["results"][0]
    assert result["doi"] == "10.1000/example"
    assert (result["row_index"], result["lane_index"], result["model_version"]) == (
        1,
        2,
        "model@revision",
    )
    assert "source_pdf" not in result and "candidate_path" not in result
    assert "/private/" not in response.text
    _, filters, limit, offset = calls[0]
    assert (filters.q, filters.target, filters.sample, filters.condition, filters.paper_id) == (
        "TP53 Nutlin",
        "TP53",
        "A549",
        "Nutlin",
        "doi:10.1000/example",
    )
    assert (limit, offset) == (1, 2)
    empty = client.get("/api/records?offset=20").json()
    assert (empty["results"], empty["count"], empty["total"]) == ([], 0, 17)
    assert client.get("/api/records?limit=201").status_code == 422


def test_detail_and_images_reject_missing_records_and_paths_outside_runs(
    client, monkeypatch, tmp_path
):
    data_dir = tmp_path / "data"
    image_path = data_dir / "runs" / "paper" / "panel_candidates" / "figure.png"
    image_path.parent.mkdir(parents=True)
    image_path.write_bytes(b"candidate image")
    record = catalog_record()
    record["candidate_path"] = str(image_path.relative_to(data_dir))
    monkeypatch.setattr(
        api.db, "get_record", lambda _, record_id: record if record_id == 7 else None
    )
    detail = client.get("/api/records/7")
    assert detail.status_code == 200
    assert detail.json()["image_url"] == "/api/records/7/image"
    assert client.get("/api/records/7/image").content == b"candidate image"
    assert client.get("/api/records/8").status_code == 404
    assert client.get("/api/records/8/image").status_code == 404

    outside = tmp_path / "outside.png"
    outside.write_bytes(b"private")
    link = image_path.parent / "link.png"
    link.symlink_to(outside)
    for value in (str(outside), "../../outside.png", str(link)):
        record["candidate_path"] = value
        assert client.get("/api/records/7/image").status_code == 404
        assert client.get("/api/records/7").json()["image_url"] is None
    assert _safe_candidate_path(str(image_path), data_dir) == image_path
    assert _safe_candidate_path("paper/panel_candidates/figure.png", data_dir) == image_path


def test_health_is_database_only_and_public_routes_cannot_write(client, monkeypatch):
    monkeypatch.setattr(api.db, "health", lambda _: True)
    assert client.get("/health").json() == {"status": "ok", "database": "ok"}
    monkeypatch.setattr(api.db, "health", lambda _: False)
    assert client.get("/health").status_code == 503
    assert all(
        route.methods <= {"GET", "HEAD"} for route in api.app.routes if isinstance(route, APIRoute)
    )
    assert client.post("/api/records", json={}).status_code == 405
    assert client.post("/api/search", json={"query": "TP53"}).status_code == 404


def test_frontend_bundle_and_assets_are_served_without_source_files(client):
    response = client.get("/")
    assert response.status_code == 200
    assert response.headers["cache-control"] == "no-cache"
    assets = re.findall(r'/assets/[^"\s]+', response.text)
    assert assets
    for asset in assets:
        response = client.get(asset)
        assert response.status_code == 200 and response.content
        assert (
            "javascript" in response.headers["content-type"]
            or "text/css" in response.headers["content-type"]
        )
    assert client.get("/src/main.tsx").status_code == 404
    assert client.get("/api/missing").status_code == 404


def test_page_context_follows_references_across_page_breaks(tmp_path) -> None:
    run_dir = tmp_path / "data" / "runs" / "paper"
    candidate_path = run_dir / "panel_candidates" / "page_005.png"
    candidate_path.parent.mkdir(parents=True)
    candidate_path.write_bytes(b"image")
    (run_dir / "pages.json").write_text(
        json.dumps(
            [
                {
                    "page": 3,
                    "text": (
                        "The interaction was evaluated before and after treatment. "
                        "As shown in Figures 2A–C, Hsp70 was bound to mutant p53, "
                        "but the detected p53 decreased in the\n"
                    ),
                },
                {
                    "page": 4,
                    "text": (
                        "input. More Hsp70 co-immunoprecipitated after treatment "
                        "(Figures 2D–F). These findings support Hsp70-mediated degradation. "
                        "Knockdown rescued mutant p53 (Figures 2G–I). Together, these "
                        "findings confirm the proposed mechanism.\n"
                        "A\nB\nFIGURE 1\nAn unrelated caption.\nfrontiersin.org\n04"
                    ),
                },
                {
                    "page": 5,
                    "text": (
                        "A different experiment is shown in Figures 3A, B.\n"
                        "A\nB\nFIGURE 2\nThe full Figure 2 caption.\nfrontiersin.org\n05"
                    ),
                },
                {
                    "page": 6,
                    "text": (
                        "A separate experiment appears in Supplementary Figure 2. "
                        "This text is unrelated."
                    ),
                },
            ]
        )
    )

    context = _page_context(candidate_path, 5, "FIGURE 2")

    assert "Figures 2A–C" in context
    assert "decreased in the input" in context
    assert "Figures 2G–I" in context
    assert "Figures 3A, B" not in context
    assert "Supplementary Figure 2" not in context
    assert "full Figure 2 caption" not in context
