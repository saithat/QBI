from __future__ import annotations

import json
import re
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Annotated, Any

from fastapi import FastAPI, HTTPException, Query
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles

import hiveblot
from hiveblot import db
from hiveblot.domain import RecordSearchCriteria
from hiveblot.model_client import LocalModelClient, ModelUnavailable, SearchFilterPrediction
from hiveblot.persistence_models import WesternBlotRecordListRow
from hiveblot.settings import get_settings

from .artifacts import router as artifact_router
from .evaluation import router as evaluation_router
from .extraction import router as extraction_router
from .golden import router as golden_router
from .metrics import router as metrics_router
from .pipeline import router as pipeline_router
from .review_queue import router as review_queue_router
from .schemas import (
    HealthResponse,
    RecordDetailResponse,
    RecordListResponse,
    SearchFiltersResponse,
    SearchRequest,
    SearchResponse,
    WesternBlotRecordResponse,
)
from .spatial_editor import router as spatial_editor_router
from .structured_editor import router as structured_editor_router
from .workbench import router as workbench_router

API_RECORD_FIELDS = frozenset(WesternBlotRecordResponse.model_fields)


@asynccontextmanager
async def lifespan(_: FastAPI) -> AsyncIterator[None]:
    db.initialize(get_settings().database_url)
    yield


app = FastAPI(title="HiveBlot", version="0.1.0", lifespan=lifespan)
app.include_router(artifact_router)
app.include_router(evaluation_router)
app.include_router(extraction_router)
app.include_router(golden_router)
app.include_router(metrics_router)
app.include_router(pipeline_router)
app.include_router(review_queue_router)
app.include_router(spatial_editor_router)
app.include_router(structured_editor_router)
app.include_router(workbench_router)
INDEX = Path(hiveblot.__file__).with_name("static") / "index.html"
REVIEW_WEB = Path(__file__).resolve().parents[1] / "web"
app.mount("/review/assets", StaticFiles(directory=REVIEW_WEB / "assets"), name="review-assets")


@app.get("/", include_in_schema=False)
def index() -> FileResponse:
    return FileResponse(INDEX)


@app.get("/review", include_in_schema=False)
def review_browser() -> FileResponse:
    return FileResponse(REVIEW_WEB / "index.html")


@app.get("/workbench/{case_id}", include_in_schema=False)
def evidence_workbench(case_id: str) -> FileResponse:
    del case_id
    return FileResponse(REVIEW_WEB / "workbench.html")


@app.get("/annotate/{case_id}", include_in_schema=False)
def structured_annotation_editor(case_id: str) -> FileResponse:
    del case_id
    return FileResponse(REVIEW_WEB / "annotate.html")


@app.get("/spatial/{case_id}", include_in_schema=False)
def spatial_annotation_editor(case_id: str) -> FileResponse:
    del case_id
    return FileResponse(REVIEW_WEB / "spatial.html")


@app.get("/metrics", include_in_schema=False)
def evaluation_metrics_dashboard() -> FileResponse:
    return FileResponse(REVIEW_WEB / "metrics.html")


@app.get("/health", response_model=HealthResponse)
async def health() -> JSONResponse:
    settings = get_settings()
    database_ok = db.health(settings.database_url)
    model_ok = await LocalModelClient(settings).health()
    ready = database_ok and model_ok
    response = HealthResponse(
        status="ok" if ready else "not_ready",
        database="ok" if database_ok else "unavailable",
        model="ok" if model_ok else "unavailable",
    )
    return JSONResponse(
        response.model_dump(mode="json"),
        status_code=200 if ready else 503,
    )


@app.get("/api/records", response_model=RecordListResponse)
def records(
    target: str | None = None,
    sample: str | None = None,
    condition: str | None = None,
    limit: Annotated[int, Query(ge=1, le=200)] = 100,
    offset: Annotated[int, Query(ge=0)] = 0,
) -> RecordListResponse:
    settings = get_settings()
    filters = RecordSearchCriteria(target=target, sample=sample, condition=condition)
    results = db.list_records(
        settings.database_url,
        filters,
        limit=limit,
        offset=offset,
    )
    return RecordListResponse(
        count=len(results),
        filters=_filters_response(filters),
        results=tuple(_record_response(record) for record in results),
    )


@app.get("/api/records/{record_id}", response_model=RecordDetailResponse)
def record_detail(record_id: int) -> RecordDetailResponse:
    settings = get_settings()
    record = db.get_record(settings.database_url, record_id)
    if record is None:
        raise HTTPException(status_code=404, detail="Record not found")

    candidate_path = _safe_candidate_path(record.get("candidate_path"), settings.data_dir)
    extraction = _candidate_extraction(candidate_path)
    warnings = extraction.get("warnings", ())
    return RecordDetailResponse(
        record=_record_response(record),
        image_url=f"/api/records/{record_id}/image" if candidate_path else None,
        figure_caption=str(extraction.get("figure_caption", "")),
        paper_context=_page_context(
            candidate_path,
            record["page"],
            record.get("figure_label"),
        ),
        warnings=tuple(str(warning) for warning in warnings) if isinstance(warnings, list) else (),
    )


@app.get("/api/records/{record_id}/image", include_in_schema=False)
def record_image(record_id: int) -> FileResponse:
    settings = get_settings()
    record = db.get_record(settings.database_url, record_id)
    if record is None:
        raise HTTPException(status_code=404, detail="Record not found")

    candidate_path = _safe_candidate_path(record.get("candidate_path"), settings.data_dir)
    if candidate_path is None:
        raise HTTPException(status_code=404, detail="Candidate image not found")
    return FileResponse(candidate_path)


@app.post("/api/search", response_model=SearchResponse)
async def search(request: SearchRequest) -> SearchResponse:
    settings = get_settings()
    client = LocalModelClient(settings)
    try:
        prediction = await client.parse_search(request.query)
    except ModelUnavailable as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc

    filters = _prediction_to_criteria(prediction)
    results = db.list_records(
        settings.database_url,
        filters,
        broad_query=request.query,
        limit=request.limit,
    )
    return SearchResponse(
        query=request.query,
        filters=_filters_response(filters),
        count=len(results),
        results=tuple(_record_response(record) for record in results),
    )


def _prediction_to_criteria(prediction: SearchFilterPrediction) -> RecordSearchCriteria:
    return RecordSearchCriteria(
        target=prediction.target,
        sample=prediction.sample,
        condition=prediction.condition,
    )


def _filters_response(filters: RecordSearchCriteria) -> SearchFiltersResponse:
    return SearchFiltersResponse(
        target=filters.target,
        sample=filters.sample,
        condition=filters.condition,
    )


def _record_response(record: WesternBlotRecordListRow) -> WesternBlotRecordResponse:
    payload: dict[str, Any] = {
        key: value for key, value in record.items() if key in API_RECORD_FIELDS
    }
    return WesternBlotRecordResponse.model_validate(payload)


def _safe_candidate_path(value: object, data_dir: Path) -> Path | None:
    if not isinstance(value, str) or not value:
        return None
    try:
        path = Path(value).resolve(strict=True)
        root = (data_dir / "runs").resolve(strict=True)
    except OSError:
        return None
    if not path.is_relative_to(root) or path.suffix.lower() not in {".png", ".jpg", ".jpeg"}:
        return None
    return path if path.is_file() else None


def _candidate_extraction(candidate_path: Path | None) -> dict[str, object]:
    if candidate_path is None:
        return {}
    stream_path = candidate_path.parent.parent / "vlm_extractions.jsonl"
    if not stream_path.is_file():
        return {}

    latest: dict[str, object] = {}
    try:
        with stream_path.open(encoding="utf-8") as handle:
            for line in handle:
                if not line.strip():
                    continue
                item = json.loads(line)
                if Path(item.get("candidate_path", "")).name != candidate_path.name:
                    continue
                extraction = item.get("extraction")
                if isinstance(extraction, dict) and extraction.get("is_western_blot") is True:
                    latest = extraction
    except (OSError, json.JSONDecodeError):
        return {}
    return latest


def _page_context(
    candidate_path: Path | None,
    page: object,
    figure_label: object,
) -> str:
    if candidate_path is None or not isinstance(page, int) or not isinstance(figure_label, str):
        return ""
    pages_path = candidate_path.parent.parent / "pages.json"
    try:
        pages = json.loads(pages_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return ""

    figure_number = re.search(r"\d+", figure_label)
    if figure_number is None:
        return ""

    nearby_text = []
    for item in pages:
        if not isinstance(item, dict) or not isinstance(item.get("text"), str):
            continue
        item_page = item.get("page")
        if isinstance(item_page, int) and page - 2 <= item_page <= page + 1:
            body = _page_body(item["text"])
            if body:
                nearby_text.append(body)

    sentences = _sentences(" ".join(nearby_text))
    if not sentences:
        return ""

    number = re.escape(figure_number.group())
    reference = re.compile(
        rf"(?<!Supplementary )\bfig(?:ure)?s?\.?\s*{number}"
        rf"(?:\s*[A-Z](?:\s*[–—-]\s*[A-Z])?)?",
        re.IGNORECASE,
    )
    matches = [index for index, sentence in enumerate(sentences) if reference.search(sentence)]
    if not matches:
        return ""

    selected: set[int] = set()
    for index in matches:
        selected.update(range(max(0, index - 2), min(len(sentences), index + 2)))

    groups: list[list[str]] = []
    for index in sorted(selected):
        if not groups or index - 1 not in selected:
            groups.append([])
        groups[-1].append(sentences[index])
    return "\n\n".join(" ".join(group) for group in groups)


def _page_body(text: str) -> str:
    lines = text.splitlines()
    figure_index = next(
        (
            index
            for index, line in enumerate(lines)
            if re.fullmatch(r"\s*(?:FIGURE|Figure)\s+\d+\s*", line)
        ),
        None,
    )
    if figure_index is not None:
        while figure_index and re.fullmatch(r"\s*[A-Z]\s*", lines[figure_index - 1]):
            figure_index -= 1
        lines = lines[:figure_index]

    lines = [
        line
        for line in lines
        if not re.fullmatch(r"\s*\d{2}\s*", line)
        and "frontiersin.org" not in line.casefold()
        and not re.fullmatch(r"\s*10\.\d{4,9}/\S+\s*", line)
        and not re.fullmatch(r"\s*\S+\s+et al\.\s*", line, re.IGNORECASE)
        and not re.fullmatch(r"\s*Frontiers in \S+(?:\s+\S+)*\s*", line)
    ]
    body = "\n".join(lines)
    body = re.sub(r"(?<=\w)-\n(?=\w)", "-", body)
    return re.sub(r"\s+", " ", body).strip()


def _sentences(text: str) -> list[str]:
    return [
        sentence.strip()
        for sentence in re.split(r"(?<=[.!?])\s+(?=[A-Z0-9(])", text)
        if sentence.strip()
    ]
