"""Anonymous, read-only catalog of extracted western blot records."""

from __future__ import annotations

import json
import re
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Annotated, Any
from urllib.parse import unquote, urlparse

from fastapi import FastAPI, HTTPException, Query
from fastapi.responses import FileResponse, JSONResponse

from . import db
from .domain import RecordSearchCriteria
from .persistence_models import WesternBlotRecordListRow
from .schemas import (
    HealthResponse,
    RecordDetailResponse,
    RecordListResponse,
    WesternBlotRecordResponse,
)
from .settings import get_settings

INDEX = Path(__file__).with_name("static") / "index.html"
API_RECORD_FIELDS = frozenset(WesternBlotRecordResponse.model_fields)


@asynccontextmanager
async def lifespan(_: FastAPI) -> AsyncIterator[None]:
    db.initialize(get_settings().database_url)
    yield


app = FastAPI(title="HiveBlot", version="0.1.0", lifespan=lifespan)


@app.get("/", include_in_schema=False)
def index() -> FileResponse:
    return FileResponse(INDEX)


@app.get("/health", response_model=HealthResponse)
def health() -> JSONResponse:
    ready = db.health(get_settings().database_url)
    return JSONResponse(
        HealthResponse(
            status="ok" if ready else "not_ready",
            database="ok" if ready else "unavailable",
        ).model_dump(),
        status_code=200 if ready else 503,
    )


@app.get("/api/records", response_model=RecordListResponse)
def records(
    q: Annotated[str | None, Query(max_length=1000)] = None,
    target: Annotated[str | None, Query(max_length=200)] = None,
    sample: Annotated[str | None, Query(max_length=200)] = None,
    condition: Annotated[str | None, Query(max_length=200)] = None,
    paper_id: Annotated[str | None, Query(max_length=500)] = None,
    limit: Annotated[int, Query(ge=1, le=200)] = 100,
    offset: Annotated[int, Query(ge=0)] = 0,
) -> RecordListResponse:
    rows, total = db.search_records(
        get_settings().database_url,
        RecordSearchCriteria(
            q=q, target=target, sample=sample, condition=condition, paper_id=paper_id
        ),
        limit=limit,
        offset=offset,
    )
    return RecordListResponse(
        results=tuple(_record_response(row) for row in rows),
        count=len(rows),
        total=total,
        limit=limit,
        offset=offset,
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
        paper_context=_page_context(candidate_path, record["page"], record.get("figure_label")),
        warnings=tuple(str(warning) for warning in warnings) if isinstance(warnings, list) else (),
    )


@app.get("/api/records/{record_id}/image", include_in_schema=False)
def record_image(record_id: int) -> FileResponse:
    settings = get_settings()
    record = db.get_record(settings.database_url, record_id)
    if record is None:
        raise HTTPException(status_code=404, detail="Record not found")
    path = _safe_candidate_path(record.get("candidate_path"), settings.data_dir)
    if path is None:
        raise HTTPException(status_code=404, detail="Candidate image not found")
    return FileResponse(path)


def _record_response(record: WesternBlotRecordListRow) -> WesternBlotRecordResponse:
    payload: dict[str, Any] = {
        key: value for key, value in record.items() if key in API_RECORD_FIELDS
    }
    source_url = payload.get("source_url")
    try:
        parsed_url = urlparse(source_url) if isinstance(source_url, str) else None
        valid_url = (
            parsed_url is not None
            and parsed_url.scheme in {"http", "https"}
            and bool(parsed_url.hostname)
        )
    except ValueError:
        valid_url = False
    if not valid_url:
        payload["source_url"] = None
    payload["doi"] = _record_doi(record["paper_id"], payload.get("source_url"))
    return WesternBlotRecordResponse.model_validate(payload)


def _record_doi(paper_id: str, source_url: object) -> str | None:
    if isinstance(source_url, str):
        parsed = urlparse(source_url)
        if parsed.hostname in {"doi.org", "dx.doi.org"}:
            value = unquote(parsed.path.lstrip("/"))
            if re.fullmatch(r"10\.\d{4,9}/\S+", value):
                return value
    value = re.sub(r"^doi:\s*", "", paper_id, flags=re.IGNORECASE)
    return value if re.fullmatch(r"10\.\d{4,9}/\S+", value) else None


def _safe_candidate_path(value: object, data_dir: Path) -> Path | None:
    if not isinstance(value, str) or not value:
        return None
    try:
        root = (data_dir / "runs").resolve(strict=True)
        supplied = Path(value)
        if supplied.is_absolute():
            path = supplied.resolve(strict=True)
        elif supplied.parts and supplied.parts[0] == "runs":
            path = (data_dir / supplied).resolve(strict=True)
        else:
            path = (root / supplied).resolve(strict=True)
    except (OSError, RuntimeError, ValueError):
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
                if not isinstance(item, dict):
                    continue
                path = item.get("candidate_path")
                if not isinstance(path, str) or Path(path).name != candidate_path.name:
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

    if not isinstance(pages, list):
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
