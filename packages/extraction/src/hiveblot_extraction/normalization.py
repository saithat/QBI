"""Permissive adapter from retained model JSON into strict extraction contracts."""

from __future__ import annotations

import hashlib
from collections.abc import Mapping
from typing import Any
from uuid import NAMESPACE_URL, UUID, uuid5

from hiveblot_contracts import (
    ModelBandState,
    ModelConfidence,
    ModelIdentifier,
    WesternBlotBandPrediction,
    WesternBlotCandidatePrediction,
    WesternBlotCandidatePredictionSet,
    WesternBlotFigureCandidateSet,
    WesternBlotLanePrediction,
    WesternBlotPanelPrediction,
    WesternBlotTargetPrediction,
)
from pydantic import ValidationError

from hiveblot.vlm_extract import parse_json

from .errors import ExtractionOutputInvalid


def normalize_candidate_predictions(
    figure_candidates: WesternBlotFigureCandidateSet,
    *,
    model: ModelIdentifier,
    prompt_version: str,
    raw_responses: Mapping[UUID, str],
) -> WesternBlotCandidatePredictionSet:
    """Normalize raw responses only after preserving them at the invocation boundary."""

    candidate_ids = {item.candidate_id for item in figure_candidates.candidates}
    if set(raw_responses) != candidate_ids:
        raise ExtractionOutputInvalid(
            "raw model responses must cover every detected figure candidate"
        )
    try:
        predictions = tuple(
            _normalize_candidate(candidate.candidate_id, raw_responses[candidate.candidate_id])
            for candidate in figure_candidates.candidates
        )
        return WesternBlotCandidatePredictionSet(
            figure_candidates=figure_candidates,
            model=model,
            prompt_version=prompt_version,
            predictions=predictions,
        )
    except ValidationError as exc:
        raise ExtractionOutputInvalid(
            "model output failed canonical western-blot validation"
        ) from exc


def _normalize_candidate(candidate_id: UUID, raw_response: str) -> WesternBlotCandidatePrediction:
    external = parse_json(raw_response)
    if not isinstance(external, dict):
        raise ExtractionOutputInvalid("model output must contain one JSON object")
    is_western_blot = external.get("is_western_blot")
    if not isinstance(is_western_blot, bool):
        raise ExtractionOutputInvalid("model output requires a boolean is_western_blot field")
    raw_hash = hashlib.sha256(raw_response.encode()).hexdigest()
    warnings = _strings(external.get("warnings"), "warnings")
    if not is_western_blot:
        reason = _optional_text(external.get("reason"))
        if reason is None:
            reason = "model classified candidate as not a western blot"
            warnings = (*warnings, "negative classification did not include a reason")
        return WesternBlotCandidatePrediction(
            candidate_id=candidate_id,
            is_western_blot=False,
            reason=reason,
            figure_label=_optional_text(external.get("figure_label")),
            figure_caption=_optional_text(external.get("figure_caption")),
            biological_sample=_optional_text(external.get("biological_sample")),
            cell_line_tissue=_optional_text(external.get("cell_line_tissue")),
            organism=_optional_text(external.get("organism")),
            sample_type=_optional_text(external.get("sample_type")),
            panels=(),
            warnings=warnings,
            raw_response_sha256=raw_hash,
        )

    raw_panels = external.get("panels")
    panels = raw_panels if isinstance(raw_panels, list) and raw_panels else [external]
    normalized_panels = tuple(
        _normalize_panel(candidate_id, index, panel, external)
        for index, panel in enumerate(panels, start=1)
        if isinstance(panel, dict)
    )
    if len(normalized_panels) != len(panels):
        raise ExtractionOutputInvalid("every model panel must be a JSON object")
    return WesternBlotCandidatePrediction(
        candidate_id=candidate_id,
        is_western_blot=True,
        reason=_optional_text(external.get("reason")),
        figure_label=_optional_text(external.get("figure_label")),
        figure_caption=_optional_text(external.get("figure_caption")),
        biological_sample=_optional_text(external.get("biological_sample")),
        cell_line_tissue=_optional_text(external.get("cell_line_tissue")),
        organism=_optional_text(external.get("organism")),
        sample_type=_optional_text(external.get("sample_type")),
        panels=normalized_panels,
        warnings=warnings,
        raw_response_sha256=raw_hash,
    )


def _normalize_panel(
    candidate_id: UUID,
    panel_index: int,
    panel: dict[str, Any],
    root: dict[str, Any],
) -> WesternBlotPanelPrediction:
    panel_id = _stable_id(candidate_id, f"panel:{panel_index}")
    raw_targets = _object_list(panel.get("targets_top_to_bottom"), "targets_top_to_bottom")
    raw_lanes = _object_list(panel.get("lanes_left_to_right"), "lanes_left_to_right")
    raw_bands = _object_list(panel.get("bands"), "bands")
    targets = tuple(
        WesternBlotTargetPrediction(
            target_id=_stable_id(panel_id, f"target:{_positive_int(item.get('row_index'))}"),
            row_index=_positive_int(item.get("row_index")),
            target=_required_text(item.get("target"), "target"),
            is_loading_control=_boolean(item.get("is_loading_control"), "is_loading_control"),
            confidence=_confidence(item.get("confidence")),
        )
        for item in raw_targets
    )
    lanes = tuple(
        WesternBlotLanePrediction(
            lane_id=_stable_id(panel_id, f"lane:{_positive_int(item.get('lane_index'))}"),
            lane_index=_positive_int(item.get("lane_index")),
            condition=_optional_text(item.get("condition")),
            confidence=_confidence(item.get("confidence")),
        )
        for item in raw_lanes
    )
    target_by_row = {item.row_index: item.target for item in targets}
    bands = tuple(_normalize_band(panel_id, item, target_by_row) for item in raw_bands)
    treatment_context = _optional_text(panel.get("treatment_context"))
    if treatment_context is None:
        treatment_context = _optional_text(root.get("treatment_context"))
    return WesternBlotPanelPrediction(
        panel_id=panel_id,
        panel_label=_optional_text(panel.get("panel_label")),
        panel_title=_optional_text(panel.get("panel_title")),
        treatment_context=treatment_context,
        targets=targets,
        lanes=lanes,
        bands=bands,
        warnings=_strings(panel.get("warnings"), "panel warnings"),
    )


def _normalize_band(
    panel_id: UUID,
    item: dict[str, Any],
    target_by_row: Mapping[int, str],
) -> WesternBlotBandPrediction:
    row_index = _positive_int(item.get("row_index"))
    lane_index = _positive_int(item.get("lane_index"))
    expected_target = target_by_row.get(row_index)
    if expected_target is None:
        raise ExtractionOutputInvalid(f"band row {row_index} does not name a target row")
    supplied_target = _optional_text(item.get("target"))
    if supplied_target is not None and supplied_target != expected_target:
        raise ExtractionOutputInvalid("band target text does not match its target row")
    try:
        state = ModelBandState(_required_text(item.get("band_state"), "band_state").casefold())
    except ValueError as exc:
        raise ExtractionOutputInvalid("band_state must be present, absent, or uncertain") from exc
    return WesternBlotBandPrediction(
        band_id=_stable_id(panel_id, f"band:{row_index}:{lane_index}"),
        row_index=row_index,
        target=expected_target,
        lane_index=lane_index,
        band_state=state,
        confidence=_confidence(item.get("confidence")),
    )


def _object_list(value: Any, label: str) -> tuple[dict[str, Any], ...]:
    if (
        not isinstance(value, list)
        or not value
        or not all(isinstance(item, dict) for item in value)
    ):
        raise ExtractionOutputInvalid(f"{label} must be a non-empty array of objects")
    return tuple(value)


def _strings(value: Any, label: str) -> tuple[str, ...]:
    if value is None:
        return ()
    if not isinstance(value, list) or not all(isinstance(item, str) for item in value):
        raise ExtractionOutputInvalid(f"{label} must be an array of strings")
    return tuple(item.strip() for item in value if item.strip())


def _required_text(value: Any, label: str) -> str:
    text = _optional_text(value)
    if text is None:
        raise ExtractionOutputInvalid(f"{label} must be a non-empty string")
    return text


def _optional_text(value: Any) -> str | None:
    if value is None:
        return None
    if not isinstance(value, str):
        raise ExtractionOutputInvalid("model text fields must be strings or null")
    text = value.strip()
    return text or None


def _positive_int(value: Any) -> int:
    if isinstance(value, bool):
        raise ExtractionOutputInvalid("row and lane indices must be positive integers")
    try:
        result = int(value)
    except (TypeError, ValueError) as exc:
        raise ExtractionOutputInvalid("row and lane indices must be positive integers") from exc
    if result < 1:
        raise ExtractionOutputInvalid("row and lane indices must be positive integers")
    return result


def _boolean(value: Any, label: str) -> bool:
    if isinstance(value, bool):
        return value
    if isinstance(value, str) and value.casefold() in {"true", "false"}:
        return value.casefold() == "true"
    raise ExtractionOutputInvalid(f"{label} must be boolean")


def _confidence(value: Any) -> ModelConfidence:
    if not isinstance(value, str):
        raise ExtractionOutputInvalid("confidence must be low, medium, or high")
    try:
        return ModelConfidence(value.strip().casefold())
    except ValueError as exc:
        raise ExtractionOutputInvalid("confidence must be low, medium, or high") from exc


def _stable_id(parent_id: UUID, key: str) -> UUID:
    return uuid5(NAMESPACE_URL, f"urn:hiveblot:western-blot:{parent_id}:{key}")
