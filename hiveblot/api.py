from __future__ import annotations

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Annotated

from fastapi import FastAPI, HTTPException, Query
from fastapi.responses import FileResponse, JSONResponse
from pydantic import BaseModel, ConfigDict, Field, field_validator

from . import db
from .model_client import LocalModelClient, ModelUnavailable, SearchFilters
from .settings import get_settings


class SearchRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    query: str = Field(min_length=1, max_length=1000)
    limit: int = Field(default=100, ge=1, le=200)

    @field_validator("query")
    @classmethod
    def query_must_have_text(cls, value: str) -> str:
        value = value.strip()
        if not value:
            raise ValueError("query cannot be blank")
        return value


@asynccontextmanager
async def lifespan(_: FastAPI) -> AsyncIterator[None]:
    db.initialize(get_settings().database_url)
    yield


app = FastAPI(title="HiveBlot", version="0.1.0", lifespan=lifespan)
INDEX = Path(__file__).with_name("static") / "index.html"


@app.get("/", include_in_schema=False)
def index() -> FileResponse:
    return FileResponse(INDEX)


@app.get("/health")
async def health() -> JSONResponse:
    settings = get_settings()
    database_ok = db.health(settings.database_url)
    model_ok = await LocalModelClient(settings).health()
    ready = database_ok and model_ok
    return JSONResponse(
        {
            "status": "ok" if ready else "not_ready",
            "database": "ok" if database_ok else "unavailable",
            "model": "ok" if model_ok else "unavailable",
        },
        status_code=200 if ready else 503,
    )


@app.get("/api/records")
def records(
    target: str | None = None,
    sample: str | None = None,
    condition: str | None = None,
    limit: Annotated[int, Query(ge=1, le=200)] = 100,
    offset: Annotated[int, Query(ge=0)] = 0,
) -> dict[str, object]:
    settings = get_settings()
    filters = SearchFilters(target=target, sample=sample, condition=condition)
    results = db.list_records(
        settings.database_url,
        filters,
        limit=limit,
        offset=offset,
    )
    return {"count": len(results), "filters": filters.model_dump(), "results": results}


@app.post("/api/search")
async def search(request: SearchRequest) -> dict[str, object]:
    settings = get_settings()
    client = LocalModelClient(settings)
    try:
        filters = await client.parse_search(request.query)
    except ModelUnavailable as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc

    results = db.list_records(
        settings.database_url,
        filters,
        broad_query=request.query,
        limit=request.limit,
    )
    return {
        "query": request.query,
        "filters": filters.model_dump(),
        "count": len(results),
        "results": results,
    }
