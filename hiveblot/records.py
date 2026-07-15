from __future__ import annotations

from pathlib import Path
from typing import Any


def determine_blot_type(target: str | None, loading: bool) -> str:
    if loading:
        return "loading_control"
    normalized = (target or "").strip().lower()
    if normalized.startswith(("p-", "phospho-", "phospho ")):
        return "phospho_signaling"
    return "total_protein"


def flatten_records(
    figures: list[dict[str, Any]],
    *,
    source_pdf: str | None = None,
) -> list[dict[str, Any]]:
    """Expand positive figure-level model output into idempotent band rows."""
    rows: list[dict[str, Any]] = []
    for figure in figures:
        extraction = figure.get("extraction")
        if not isinstance(extraction, dict) or extraction.get("is_western_blot") is not True:
            continue

        candidate_path = str(figure.get("candidate_path") or "")
        candidate_key = Path(candidate_path).stem or f"page-{figure.get('page', 'unknown')}"
        for panel in _iter_panels(extraction):
            sample = (
                panel.get("cell_line_tissue")
                or panel.get("biological_sample")
                or panel.get("sample_type")
                or None
            )
            lane_lookup = {
                item.get("lane_index"): item.get("condition")
                for item in panel.get("lanes_left_to_right", [])
                if isinstance(item, dict)
            }
            row_lookup = {
                item.get("row_index"): item
                for item in panel.get("targets_top_to_bottom", [])
                if isinstance(item, dict)
            }
            target_lookup = {
                item.get("target"): item
                for item in panel.get("targets_top_to_bottom", [])
                if isinstance(item, dict) and item.get("target")
            }

            for band in panel.get("bands", []):
                if not isinstance(band, dict):
                    continue
                row_index = _positive_int_or_none(band.get("row_index"))
                lane_index = _positive_int_or_none(band.get("lane_index"))
                target_info = (
                    row_lookup.get(row_index) or target_lookup.get(band.get("target")) or {}
                )
                target = str(band.get("target") or target_info.get("target") or "").strip()
                state = str(band.get("band_state") or "uncertain").strip().lower()
                if not target or state not in {"present", "absent", "uncertain"}:
                    continue
                loading = bool(target_info.get("is_loading_control", False))
                rows.append(
                    {
                        "paper_id": str(figure.get("paper_id") or "unknown"),
                        "candidate_key": candidate_key,
                        "source_pdf": source_pdf,
                        "candidate_path": candidate_path or None,
                        "page": max(1, int(figure.get("page") or 1)),
                        "figure_label": _text_or_none(panel.get("figure_label")),
                        "panel_label": _text_or_none(panel.get("panel_label")),
                        "row_index": row_index,
                        "lane_index": lane_index,
                        "target": target,
                        "is_loading_control": loading,
                        "western_blot_type": determine_blot_type(target, loading),
                        "sample": _text_or_none(sample),
                        "organism": _text_or_none(panel.get("organism")),
                        "treatment_context": _text_or_none(panel.get("treatment_context")),
                        "condition": _text_or_none(lane_lookup.get(lane_index)),
                        "band_state": state,
                        "confidence": _confidence_value(
                            band.get("confidence") or target_info.get("confidence")
                        ),
                    }
                )
    return rows


def _iter_panels(extraction: dict[str, Any]) -> list[dict[str, Any]]:
    panels = extraction.get("panels")
    if not isinstance(panels, list) or not panels:
        return [extraction]
    root_context = {
        key: extraction.get(key)
        for key in (
            "figure_label",
            "figure_caption",
            "biological_sample",
            "cell_line_tissue",
            "organism",
            "sample_type",
            "treatment_context",
        )
    }
    merged_panels = []
    for panel in panels:
        if not isinstance(panel, dict):
            continue
        merged = {**root_context, **panel}
        for key, value in root_context.items():
            if _text_or_none(panel.get(key)) is None:
                merged[key] = value
        merged_panels.append(merged)
    return merged_panels


def _text_or_none(value: Any) -> str | None:
    if value is None:
        return None
    text = str(value).strip()
    return text or None


def _positive_int_or_none(value: Any) -> int | None:
    try:
        integer = int(value)
    except (TypeError, ValueError):
        return None
    return integer if integer > 0 else None


def _confidence_value(value: Any) -> float | None:
    if isinstance(value, (int, float)):
        return min(1.0, max(0.0, float(value)))
    if not isinstance(value, str):
        return None
    return {"high": 0.9, "medium": 0.6, "low": 0.3}.get(value.strip().lower())
