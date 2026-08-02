from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any

from . import db, pdf_preprocess, vlm_extract
from .records import flatten_records
from .settings import Settings


def run_pdf_pipeline(
    pdf_path: str | Path,
    *,
    settings: Settings | None = None,
    use_cache: bool = True,
    out_dir: str | Path | None = None,
) -> dict[str, Any]:
    settings = settings or Settings.from_env()
    pdf_path = Path(pdf_path).resolve()
    if not pdf_path.is_file():
        raise FileNotFoundError(f"PDF not found: {pdf_path}")

    db.initialize(settings.database_url)
    preprocess = pdf_preprocess.preprocess_pdf(
        pdf_path=pdf_path,
        out_dir=out_dir,
        dpi=settings.pdf_dpi,
        min_candidate_score=settings.min_candidate_score,
        min_llm_score=settings.min_vlm_score,
        data_dir=settings.data_dir,
    )

    def upsert_positive(record: dict[str, Any]) -> int:
        rows = flatten_records([record], source_pdf=str(pdf_path))
        return db.upsert_records(settings.database_url, rows)

    vlm = vlm_extract.run_vlm_extraction(
        run_dir=preprocess["out_dir"],
        base_url=settings.vllm_base_url,
        api_key=settings.vllm_api_key.get_secret_value(),
        model=settings.vllm_model,
        max_tokens=settings.vllm_max_tokens,
        timeout=int(settings.vllm_timeout_seconds),
        image_max_side=settings.vllm_image_max_side,
        resume=use_cache,
        on_positive=upsert_positive,
    )

    positives = json.loads(Path(vlm["positives_json"]).read_text(encoding="utf-8"))
    all_rows = flatten_records(positives, source_pdf=str(pdf_path))
    upserted = db.upsert_records(settings.database_url, all_rows)
    summary = {
        "paper_id": preprocess["paper_id"],
        "run_dir": preprocess["out_dir"],
        "pages": preprocess["pages"],
        "candidates": preprocess["candidate_count"],
        "vlm_candidates": preprocess["llm_candidate_count"],
        "positive_candidates": vlm["positive_results"],
        "database_rows": upserted,
        "cached_results": vlm["cached_results"],
        "queried_candidates": vlm["queried_candidates"],
    }
    Path(preprocess["out_dir"], "pipeline_summary.json").write_text(
        json.dumps(summary, indent=2),
        encoding="utf-8",
    )
    return summary


def main() -> None:
    parser = argparse.ArgumentParser(description="Extract western blot records from a local PDF.")
    parser.add_argument("pdf", type=Path, help="PDF path, normally under /data/input")
    parser.add_argument("--out-dir", type=Path, help="Override the generated run directory")
    parser.add_argument(
        "--no-cache",
        action="store_true",
        help="Re-query every candidate instead of resuming successful VLM output",
    )
    args = parser.parse_args()
    summary = run_pdf_pipeline(
        args.pdf,
        use_cache=not args.no_cache,
        out_dir=args.out_dir,
    )
    print(json.dumps(summary, indent=2))


if __name__ == "__main__":
    main()
