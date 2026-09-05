"""Maintainer ingestion: extract a complete source before replacing its public records."""

from __future__ import annotations

import argparse
import hashlib
import json
import re
import shutil
import tempfile
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit

from PIL import Image
from pydantic import TypeAdapter

from . import db, pdf_preprocess, vlm_extract
from .persistence_models import WesternBlotRecordWrite
from .records import flatten_records
from .settings import Settings

PIPELINE_VERSION = "2"
IMAGE_SUFFIXES = {".png", ".jpg", ".jpeg"}


def run_pipeline(
    source_path: str | Path,
    *,
    settings: Settings | None = None,
    paper_id: str | None = None,
    source_id: str | None = None,
    source_url: str | None = None,
    context: str = "",
    use_cache: bool = True,
    out_dir: str | Path | None = None,
) -> dict[str, Any]:
    settings = settings or Settings.from_env()
    source_path = Path(source_path).resolve()
    if not source_path.is_file():
        raise FileNotFoundError(f"Source not found: {source_path}")
    suffix = source_path.suffix.lower()
    if suffix not in IMAGE_SUFFIXES | {".pdf"}:
        raise ValueError("Source must be a PDF, PNG, or JPEG file")
    if source_url is not None:
        source_url = source_url.strip()
        parsed = urlsplit(source_url)
        if parsed.scheme not in {"http", "https"} or not parsed.hostname:
            raise ValueError("Source URL must be an HTTP(S) URL")
    if paper_id is not None and not paper_id.strip():
        raise ValueError("Paper ID must not be blank")
    if source_id is not None and not source_id.strip():
        raise ValueError("Source ID must not be blank")
    source_id = source_id.strip() if source_id is not None else source_path.name

    content = source_path.read_bytes()
    source_sha256 = hashlib.sha256(content).hexdigest()
    paper_id = (paper_id.strip() if paper_id is not None else None) or (
        pdf_preprocess.extract_paper_doi(source_path) if suffix == ".pdf" else None
    )
    paper_id = paper_id or source_path.stem
    model_version = f"{settings.vllm_model}@{settings.vllm_model_revision}"
    configuration = {
        "pipeline_version": PIPELINE_VERSION,
        "source_sha256": source_sha256,
        "paper_id": paper_id,
        "source_id": source_id,
        "source_url": source_url,
        "context": context,
        "pdf_dpi": settings.pdf_dpi,
        "min_candidate_score": settings.min_candidate_score,
        "min_vlm_score": settings.min_vlm_score,
        "model_version": model_version,
        "base_url": settings.vllm_base_url,
        "max_tokens": settings.vllm_max_tokens,
        "timeout": settings.vllm_timeout_seconds,
        "image_max_side": settings.vllm_image_max_side,
        "prompt": vlm_extract.PROMPT,
        "response_schema": vlm_extract.EXTRACTION_SCHEMA,
    }
    fingerprint = hashlib.sha256(
        json.dumps(configuration, sort_keys=True, separators=(",", ":")).encode()
    ).hexdigest()
    data_dir = settings.data_dir.resolve()
    runs_dir = data_dir / "runs"
    slug = re.sub(r"[^A-Za-z0-9._-]+", "_", paper_id).strip("._-")[:80] or "source"
    run_parent = Path(out_dir).resolve() if out_dir is not None else runs_dir / slug
    run_dir = (run_parent / fingerprint).resolve()
    if not run_dir.is_relative_to(runs_dir.resolve()):
        raise ValueError("Output directory must be beneath the configured data/runs directory")
    run_dir.mkdir(parents=True, exist_ok=True)
    managed_source = run_dir / f"source{suffix}"
    managed_source.write_bytes(content)
    _write_json(run_dir / "source.json", configuration)

    if suffix == ".pdf":
        preprocess = pdf_preprocess.preprocess_pdf(
            pdf_path=managed_source,
            paper_id=paper_id,
            out_dir=run_dir,
            dpi=settings.pdf_dpi,
            min_candidate_score=settings.min_candidate_score,
            min_llm_score=settings.min_vlm_score,
            data_dir=data_dir,
        )
        if context:
            contexts = run_dir / "candidate_contexts.jsonl"
            entries = [json.loads(line) for line in contexts.read_text().splitlines() if line]
            for entry in entries:
                entry["text_context"] = f"{context}\n\n{entry['text_context']}"[:12000]
            contexts.write_text(
                "".join(json.dumps(entry) + "\n" for entry in entries), encoding="utf-8"
            )
    else:
        preprocess = _prepare_image(managed_source, run_dir, paper_id, context)
    if not preprocess["llm_candidate_count"]:
        raise RuntimeError("No candidate images were found; existing catalog records were kept")

    vlm = vlm_extract.run_vlm_extraction(
        run_dir=run_dir,
        base_url=settings.vllm_base_url,
        api_key=settings.vllm_api_key.get_secret_value(),
        model=settings.vllm_model,
        max_tokens=settings.vllm_max_tokens,
        timeout=int(settings.vllm_timeout_seconds),
        image_max_side=settings.vllm_image_max_side,
        resume=use_cache,
    )
    records = _validated_records(run_dir, data_dir, paper_id, managed_source)
    for record in records:
        record["source_id"] = source_id
        record["candidate_key"] = (
            f"{hashlib.sha256(source_id.encode()).hexdigest()[:16]}-{record['candidate_key']}"
        )
        record["model_version"] = model_version
        record["source_url"] = source_url
    records = TypeAdapter(list[WesternBlotRecordWrite]).validate_python(records, strict=True)
    identities = {
        (
            row["candidate_key"],
            row["panel_label"],
            row["row_index"],
            row["lane_index"],
            row["target"],
        )
        for row in records
    }
    if len(identities) != len(records):
        raise ValueError(
            "Extraction has duplicate band identities; existing catalog records were kept"
        )

    db.initialize(settings.database_url)
    published_dir = _snapshot_source(run_dir, data_dir, managed_source, records)
    replaced = db.replace_records_for_source(
        settings.database_url, paper_id, records, source_id=source_id
    )
    summary = {
        "paper_id": paper_id,
        "source_id": source_id,
        "source_sha256": source_sha256,
        "source_url": source_url,
        "model_version": model_version,
        "run_dir": str(run_dir),
        "published_dir": str(published_dir),
        "pages": preprocess["pages"],
        "candidates": preprocess["candidate_count"],
        "vlm_candidates": preprocess["llm_candidate_count"],
        "positive_candidates": vlm["positive_results"],
        "database_rows": replaced,
        "cached_results": vlm["cached_results"],
        "queried_candidates": vlm["queried_candidates"],
    }
    _write_json(run_dir / "pipeline_summary.json", summary)
    return summary


def _snapshot_source(
    run_dir: Path,
    data_dir: Path,
    source: Path,
    records: list[WesternBlotRecordWrite],
) -> Path:
    """Publish immutable assets so retries cannot alter an existing record's evidence."""
    snapshot = Path(tempfile.mkdtemp(prefix="published-", dir=run_dir))
    # The container's public service runs as a different user from the maintainer CLI.
    snapshot.chmod(0o755)
    for name in (source.name, "source.json", "pages.json", "vlm_extractions.jsonl"):
        shutil.copyfile(run_dir / name, snapshot / name)
    shutil.copytree(run_dir / "panel_candidates", snapshot / "panel_candidates")
    for record in records:
        candidate = Path(record["candidate_path"] or "")
        record["candidate_path"] = (
            (snapshot / "panel_candidates" / candidate.name).relative_to(data_dir).as_posix()
        )
        record["source_pdf"] = (snapshot / source.name).relative_to(data_dir).as_posix()
    return snapshot


def _prepare_image(source: Path, run_dir: Path, paper_id: str, context: str) -> dict[str, Any]:
    candidate_dir = run_dir / "panel_candidates"
    candidate_dir.mkdir(exist_ok=True)
    candidate_path = candidate_dir / "page_001_cand_0000.png"
    with Image.open(source) as image:
        if image.format not in {"PNG", "JPEG"} or getattr(image, "n_frames", 1) != 1:
            raise ValueError("Image ingestion requires a single-frame PNG or JPEG")
        rgb = image.convert("RGB")
        rgb.save(candidate_path)
        width, height = rgb.size
    candidate = {
        "paper_id": paper_id,
        "page": 1,
        "bbox_page": [0, 0, width, height],
        "tight_bbox_page": [0, 0, width, height],
        "candidate_path": str(candidate_path),
        "cv_score": 1.0,
    }
    _write_json(run_dir / "candidates.json", [candidate])
    _write_json(run_dir / "llm_candidates.json", [candidate])
    _write_json(
        run_dir / "pages.json",
        [
            {
                "page": 1,
                "image_path": str(candidate_path),
                "text": context,
                "width_px": width,
                "height_px": height,
            }
        ],
    )
    (run_dir / "candidate_contexts.jsonl").write_text(
        json.dumps({"candidate_path": str(candidate_path), "page": 1, "text_context": context})
        + "\n",
        encoding="utf-8",
    )
    return {"pages": 1, "candidate_count": 1, "llm_candidate_count": 1}


def _validated_records(
    run_dir: Path, data_dir: Path, paper_id: str, source: Path
) -> list[WesternBlotRecordWrite]:
    candidates = json.loads((run_dir / "llm_candidates.json").read_text())
    expected_paths = {candidate["candidate_path"] for candidate in candidates}
    results = json.loads((run_dir / "vlm_extractions.json").read_text())
    if not isinstance(results, list) or len(results) != len(expected_paths):
        raise RuntimeError("Extraction results are incomplete; existing catalog records were kept")
    rows: list[WesternBlotRecordWrite] = []
    seen: set[str] = set()
    for result in results:
        candidate_path = result.get("candidate_path") if isinstance(result, dict) else None
        if (
            not isinstance(candidate_path, str)
            or candidate_path not in expected_paths
            or candidate_path in seen
        ):
            raise RuntimeError("Extraction results do not match the source candidates")
        seen.add(candidate_path)
        extraction = result.get("extraction")
        if vlm_extract._should_retry_extraction(extraction):
            raise RuntimeError("A candidate extraction failed; existing catalog records were kept")
        if result.get("paper_id") != paper_id:
            raise ValueError("Extraction paper ID does not match the source")
        path = Path(candidate_path).resolve()
        if not path.is_relative_to(run_dir) or not path.is_file():
            raise ValueError("Candidate image must exist beneath the managed source run directory")
        extracted = flatten_records([result], source_pdf=source.relative_to(data_dir).as_posix())
        if extraction["is_western_blot"] and not extracted:
            raise ValueError("A positive extraction has no valid band records")
        image_sha256 = hashlib.sha256(path.read_bytes()).hexdigest()
        for row in extracted:
            row["candidate_path"] = path.relative_to(data_dir).as_posix()
            row["image_sha256"] = image_sha256
        rows.extend(extracted)
    return rows


def _write_json(path: Path, payload: Any) -> None:
    path.write_text(json.dumps(payload, indent=2), encoding="utf-8")


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Extract catalog records from a PDF or blot image."
    )
    parser.add_argument("source", type=Path, help="Local PDF, PNG, or JPEG")
    parser.add_argument("--paper-id", help="Publication DOI or stable source identifier")
    parser.add_argument("--source-id", help="Stable identifier for this input (default: filename)")
    parser.add_argument("--source-url", help="Public HTTP(S) citation URL")
    parser.add_argument("--context", default="", help="Literal caption or source text")
    parser.add_argument("--out-dir", type=Path, help="Run parent directory beneath data/runs")
    parser.add_argument("--no-cache", action="store_true", help="Re-query successful candidates")
    args = parser.parse_args()
    try:
        summary = run_pipeline(
            args.source,
            paper_id=args.paper_id,
            source_id=args.source_id,
            source_url=args.source_url,
            context=args.context,
            use_cache=not args.no_cache,
            out_dir=args.out_dir,
        )
    except (OSError, ValueError, RuntimeError) as error:
        parser.exit(1, f"Ingestion failed: {error}\n")
    print(json.dumps(summary, indent=2))


if __name__ == "__main__":
    main()
