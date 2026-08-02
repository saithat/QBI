"""PDF preprocessing for local western blot candidate extraction.

This module contains the non-LLM stages from ``pipeline.ipynb``:
rendering PDF pages, retrieving local paper text for candidate crops, and
generating OpenCV panel candidates that can later be sent to a vision model.
"""

from __future__ import annotations

import json
import os
import re
from collections.abc import Iterable
from pathlib import Path
from typing import Any

DEFAULT_DPI = 350
DEFAULT_MIN_CANDIDATE_SCORE = 0.35
DEFAULT_MIN_LLM_SCORE = 0.65
DOI_RE = re.compile(r"\b10\.\d{4,9}/[-._;()/:A-Z0-9]+", re.IGNORECASE)

BIO_SAMPLE_KEYWORDS = [
    "cell",
    "cells",
    "cell line",
    "cell lines",
    "tissue",
    "tissues",
    "tumor",
    "tumour",
    "lysate",
    "lysates",
    "sample",
    "samples",
    "mouse",
    "mice",
    "rat",
    "human",
    "patient",
    "primary",
    "culture",
    "cultured",
    "organoid",
    "xenograft",
    "biopsy",
    "fibroblast",
    "macrophage",
    "neuron",
    "epithelial",
    "breast",
    "colon",
    "lung",
    "liver",
    "brain",
    "kidney",
    "western blot",
    "immunoblot",
    "immunoblotting",
]


def normalize_ws(text: str | None) -> str:
    """Collapse repeated whitespace in extracted PDF text."""
    return re.sub(r"\s+", " ", text or "").strip()


def render_pdf(
    pdf_path: str | Path,
    out_dir: str | Path,
    dpi: int = DEFAULT_DPI,
) -> list[dict[str, Any]]:
    """Render each PDF page to PNG and return page metadata with page text."""
    try:
        import fitz
    except ImportError as exc:  # pragma: no cover - depends on local env
        raise RuntimeError("PyMuPDF is required for PDF rendering") from exc

    pdf_path = Path(pdf_path)
    out_dir = Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    scale = dpi / 72
    page_records: list[dict[str, Any]] = []

    with fitz.open(pdf_path) as doc:
        for page_idx, page in enumerate(doc):
            page_num = page_idx + 1
            pix = page.get_pixmap(matrix=fitz.Matrix(scale, scale), alpha=False)
            image_path = out_dir / f"page_{page_num:03d}.png"
            pix.save(image_path)
            page_records.append(
                {
                    "page": page_num,
                    "image_path": str(image_path),
                    "text": page.get_text(),
                    "width_px": pix.width,
                    "height_px": pix.height,
                }
            )

    return page_records


def full_paper_text(page_records: Iterable[dict[str, Any]]) -> str:
    """Join page text with stable page markers for downstream prompts."""
    return "\n\n".join(
        f"--- PAGE {record['page']} ---\n{record.get('text', '')}" for record in page_records
    )


def extract_paper_doi(
    pdf_path: str | Path,
    page_records: Iterable[dict[str, Any]] | None = None,
    max_pages: int = 3,
) -> str | None:
    """Extract the current paper DOI from PDF metadata or early page text."""
    pdf_path = Path(pdf_path)
    candidates: list[str] = []

    try:
        import fitz
    except ImportError:
        fitz = None

    if fitz is not None:
        with fitz.open(pdf_path) as doc:
            metadata = doc.metadata or {}
            metadata_text = "\n".join(str(value) for value in metadata.values() if value)
            candidates.extend(_extract_dois(metadata_text))

            if page_records is None:
                for page in doc[:max_pages]:
                    candidates.extend(_extract_dois(page.get_text()))

    if page_records is not None:
        for record in list(page_records)[:max_pages]:
            candidates.extend(_extract_dois(record.get("text", "")))

    return candidates[0] if candidates else None


def nearby_page_text(
    page_records: list[dict[str, Any]],
    page_num: int,
    radius: int = 1,
    max_chars_per_page: int = 2500,
) -> str:
    """Return normalized text around a candidate crop's source page."""
    chunks = []
    for page in range(page_num - radius, page_num + radius + 1):
        if 1 <= page <= len(page_records):
            text = normalize_ws(page_records[page - 1].get("text", ""))
            chunks.append(f"--- PAGE {page} ---\n{text[:max_chars_per_page]}")
    return "\n\n".join(chunks)


def extract_figure_context(full_text: str, max_chars: int = 5000) -> str:
    """Retrieve likely figure and caption context from full paper text."""
    text = normalize_ws(full_text)
    patterns = [
        r"\bFigure\s+\d+",
        r"\bFig\.\s*\d+",
        r"\bFigure\s+S\d+",
        r"\bFig\.\s*S\d+",
        r"\bSupplementary\s+Figure\s+\d+",
        r"\bSupplementary\s+Fig\.\s*\d+",
        r"\bExtended\s+Data\s+Fig\.\s*\d+",
    ]

    matches: list[re.Match[str]] = []
    for pattern in patterns:
        matches.extend(re.finditer(pattern, text, flags=re.IGNORECASE))

    chunks = []
    for match in matches:
        start = max(0, match.start() - 300)
        end = min(len(text), match.start() + 2500)
        chunks.append(text[start:end])

    return "\n\n".join(_dedupe_prefix(chunks))[:max_chars]


def retrieve_relevant_text_snippets(
    full_text: str,
    query_terms: Iterable[str],
    max_snippets: int = 16,
    window: int = 350,
) -> str:
    """Retrieve text snippets around biological sample and method keywords."""
    text = normalize_ws(full_text)
    low = text.lower()
    snippets = []

    for term in query_terms:
        term_low = term.lower()
        start = 0
        while True:
            idx = low.find(term_low, start)
            if idx == -1:
                break
            snippet_start = max(0, idx - window)
            snippet_end = min(len(text), idx + len(term) + window)
            snippets.append(text[snippet_start:snippet_end])
            start = idx + len(term_low)
            if len(snippets) >= max_snippets * 3:
                break

    deduped = _dedupe_prefix(snippets)
    return "\n\n".join(f"- {snippet}" for snippet in deduped[:max_snippets])


def build_text_context_for_candidate(
    candidate: dict[str, Any],
    page_records: list[dict[str, Any]],
    paper_text: str,
    max_chars: int = 12000,
) -> str:
    """Build the text block sent with a candidate image to an LLM."""
    nearby = nearby_page_text(
        page_records,
        candidate["page"],
        radius=1,
        max_chars_per_page=2500,
    )
    figure_context = extract_figure_context(paper_text, max_chars=5000)
    sample_methods_context = retrieve_relevant_text_snippets(
        paper_text,
        BIO_SAMPLE_KEYWORDS,
        max_snippets=16,
        window=350,
    )

    context = f"""
PAPER_ID: {candidate["paper_id"]}
CROP_PAGE: {candidate["page"]}
CROP_BBOX_PAGE: {candidate["bbox_page"]}

NEARBY_PAGE_TEXT:
{nearby}

LIKELY_FIGURE_OR_CAPTION_CONTEXT:
{figure_context}

PAPER_SAMPLE_OR_METHODS_SNIPPETS:
{sample_methods_context}
"""
    return context[:max_chars]


def expand_bbox(
    x: int,
    y: int,
    w: int,
    h: int,
    page_width: int,
    page_height: int,
    left: int = 240,
    top: int = 60,
    right: int = 20,
    bottom: int = 20,
) -> tuple[int, int, int, int]:
    """Expand a tight contour bbox to include nearby labels."""
    x0 = max(0, x - left)
    y0 = max(0, y - top)
    x1 = min(page_width, x + w + right)
    y1 = min(page_height, y + h + bottom)
    return x0, y0, x1, y1


def candidate_score(img_bgr: Any) -> float:
    """Score whether an image crop resembles a western blot panel."""
    try:
        import cv2
        import numpy as np
    except ImportError as exc:  # pragma: no cover - depends on local env
        raise RuntimeError("opencv-python-headless and numpy are required") from exc

    h, w = img_bgr.shape[:2]
    if w < 180 or h < 80:
        return 0.0

    area = w * h
    aspect = w / max(h, 1)
    if area < 40_000 or aspect < 0.6 or aspect > 8.0:
        return 0.0

    hsv = cv2.cvtColor(img_bgr, cv2.COLOR_BGR2HSV)
    sat = hsv[:, :, 1].mean()
    gray = cv2.cvtColor(img_bgr, cv2.COLOR_BGR2GRAY)
    dark_frac = (gray < 210).mean()
    if dark_frac < 0.01 or dark_frac > 0.65:
        return 0.0

    binary = (gray < 210).astype(np.uint8) * 255
    horizontal_kernel = cv2.getStructuringElement(cv2.MORPH_RECT, (35, 3))
    horizontal = cv2.morphologyEx(binary, cv2.MORPH_OPEN, horizontal_kernel)
    horizontal_frac = (horizontal > 0).mean()
    inv = 255 - gray
    row_profile = inv.mean(axis=1)
    col_profile = inv.mean(axis=0)
    row_signal = row_profile.std() / (row_profile.mean() + 1e-6)
    col_signal = col_profile.std() / (col_profile.mean() + 1e-6)

    score = 0.0
    score += max(0, 1 - sat / 80) * 0.25
    score += min(dark_frac / 0.18, 1) * 0.20
    score += min(horizontal_frac / 0.08, 1) * 0.30
    score += min(row_signal / 1.8, 1) * 0.15
    score += min(col_signal / 2.5, 1) * 0.10
    if sat > 70:
        score *= 0.4

    return float(score)


def generate_candidates(
    page_records: Iterable[dict[str, Any]],
    out_dir: str | Path,
    paper_id: str,
    min_score: float = DEFAULT_MIN_CANDIDATE_SCORE,
) -> list[dict[str, Any]]:
    """Generate and score panel candidate crops from rendered pages."""
    try:
        import cv2
        import numpy as np
    except ImportError as exc:  # pragma: no cover - depends on local env
        raise RuntimeError("opencv-python-headless and numpy are required") from exc

    out_dir = Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    records: list[dict[str, Any]] = []

    for page_record in page_records:
        page_num = page_record["page"]
        img = cv2.imread(str(page_record["image_path"]))
        if img is None:
            continue

        gray = cv2.cvtColor(img, cv2.COLOR_BGR2GRAY)
        page_height, page_width = gray.shape
        mask = (gray < 245).astype(np.uint8) * 255
        kernel = cv2.getStructuringElement(cv2.MORPH_RECT, (45, 45))
        merged = cv2.morphologyEx(mask, cv2.MORPH_CLOSE, kernel)
        merged = cv2.dilate(merged, kernel, iterations=1)
        contours, _ = cv2.findContours(
            merged,
            cv2.RETR_EXTERNAL,
            cv2.CHAIN_APPROX_SIMPLE,
        )

        for contour in contours:
            x, y, w, h = cv2.boundingRect(contour)
            if w * h < 40_000:
                continue
            if w > 0.95 * page_width and h > 0.95 * page_height:
                continue

            tight_crop = img[y : y + h, x : x + w]
            score = candidate_score(tight_crop)
            if score < min_score:
                continue

            x0, y0, x1, y1 = expand_bbox(x, y, w, h, page_width, page_height)
            expanded_crop = img[y0:y1, x0:x1]
            crop_path = out_dir / f"page_{page_num:03d}_cand_{len(records):04d}.png"
            cv2.imwrite(str(crop_path), expanded_crop)
            records.append(
                {
                    "paper_id": paper_id,
                    "page": page_num,
                    "bbox_page": [int(x0), int(y0), int(x1), int(y1)],
                    "tight_bbox_page": [int(x), int(y), int(x + w), int(y + h)],
                    "candidate_path": str(crop_path),
                    "cv_score": score,
                }
            )

    records.sort(key=lambda record: record["cv_score"], reverse=True)
    return records


def filter_candidates_for_llm(
    candidates: Iterable[dict[str, Any]],
    min_score: float = DEFAULT_MIN_LLM_SCORE,
) -> list[dict[str, Any]]:
    """Return candidates above the score threshold used for LLM extraction."""
    return [
        candidate for candidate in candidates if float(candidate.get("cv_score", 0.0)) >= min_score
    ]


def load_for_vlm(path: str | Path, max_side: int = 1200) -> Any:
    """Load and downsize a crop to the PIL RGB image expected by VLMs."""
    try:
        from PIL import Image
    except ImportError as exc:  # pragma: no cover - depends on local env
        raise RuntimeError("Pillow is required for VLM image loading") from exc

    img = Image.open(path).convert("RGB")
    width, height = img.size
    scale = min(1.0, max_side / max(width, height))
    if scale < 1.0:
        img = img.resize((int(width * scale), int(height * scale)))
    return img


def preprocess_pdf(
    pdf_path: str | Path,
    paper_id: str | None = None,
    out_dir: str | Path | None = None,
    dpi: int = DEFAULT_DPI,
    min_candidate_score: float = DEFAULT_MIN_CANDIDATE_SCORE,
    min_llm_score: float = DEFAULT_MIN_LLM_SCORE,
    data_dir: str | Path | None = None,
) -> dict[str, Any]:
    """Run all pre-LLM preprocessing and write JSON manifests."""
    pdf_path = Path(pdf_path)
    extracted_doi = extract_paper_doi(pdf_path)
    paper_id = paper_id or extracted_doi or pdf_path.stem
    out_dir_name = _safe_path_segment(paper_id)
    data_dir = Path(data_dir or os.environ.get("HIVEBLOT_DATA_DIR", "data"))
    default_out_dir = data_dir / "runs" / out_dir_name
    out_dir = Path(out_dir) if out_dir is not None else default_out_dir
    page_dir = out_dir / "rendered_pages"
    candidate_dir = out_dir / "panel_candidates"
    context_path = out_dir / "candidate_contexts.jsonl"
    out_dir.mkdir(parents=True, exist_ok=True)

    pages = render_pdf(pdf_path, page_dir, dpi=dpi)
    if extracted_doi is None:
        extracted_doi = extract_paper_doi(pdf_path, pages)
        if paper_id == pdf_path.stem and extracted_doi:
            paper_id = extracted_doi
    paper_text = full_paper_text(pages)
    candidates = generate_candidates(
        pages,
        candidate_dir,
        paper_id=paper_id,
        min_score=min_candidate_score,
    )
    llm_candidates = filter_candidates_for_llm(candidates, min_score=min_llm_score)

    contexts_written = 0
    with context_path.open("w", encoding="utf-8") as handle:
        for candidate in llm_candidates:
            context = build_text_context_for_candidate(candidate, pages, paper_text)
            handle.write(
                json.dumps(
                    {
                        "candidate_path": candidate["candidate_path"],
                        "page": candidate["page"],
                        "text_context": context,
                    }
                )
                + "\n"
            )
            contexts_written += 1

    _write_json(out_dir / "pages.json", pages)
    _write_json(out_dir / "candidates.json", candidates)
    _write_json(out_dir / "llm_candidates.json", llm_candidates)

    summary = {
        "paper_id": paper_id,
        "extracted_doi": extracted_doi,
        "pdf_path": str(pdf_path),
        "out_dir": str(out_dir),
        "dpi": dpi,
        "pages": len(pages),
        "candidate_count": len(candidates),
        "llm_candidate_count": len(llm_candidates),
        "contexts_written": contexts_written,
        "page_dir": str(page_dir),
        "candidate_dir": str(candidate_dir),
        "contexts_path": str(context_path),
        "top_candidates": [
            {
                "page": candidate["page"],
                "cv_score": round(float(candidate["cv_score"]), 3),
                "candidate_path": candidate["candidate_path"],
            }
            for candidate in candidates[:20]
        ],
    }
    _write_json(out_dir / "summary.json", summary)
    return summary


def _dedupe_prefix(chunks: Iterable[str], prefix_len: int = 250) -> list[str]:
    seen = set()
    deduped = []
    for chunk in chunks:
        key = chunk[:prefix_len].lower()
        if key not in seen:
            seen.add(key)
            deduped.append(chunk)
    return deduped


def _extract_dois(text: str | None) -> list[str]:
    dois = []
    for match in DOI_RE.finditer(text or ""):
        doi = match.group(0).rstrip(".,;)]}<>")
        if doi and doi not in dois:
            dois.append(doi)
    return dois


def _safe_path_segment(value: str) -> str:
    segment = re.sub(r"[^A-Za-z0-9._-]+", "_", value).strip("._")
    return segment or "paper"


def _write_json(path: Path, payload: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2), encoding="utf-8")
