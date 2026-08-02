"""Schema-constrained extraction through the local vLLM server."""

from __future__ import annotations

import base64
import io
import json
import os
from collections.abc import Callable
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from . import pdf_preprocess

DEFAULT_VLM_MODEL = "Qwen/Qwen3-VL-8B-Instruct"
DEFAULT_MAX_TOKENS = 4096
DEFAULT_TIMEOUT = 300
DEFAULT_IMAGE_MAX_SIDE = 1800

PROMPT = """
You are given ONE cropped candidate image from a scientific paper and paper text.

Return ONLY valid JSON. No markdown. No prose.

Use the IMAGE as the source of truth for:
- whether this is a western blot/immunoblot/protein band panel
- visible target labels
- visible lanes
- whether each band is present, absent, or uncertain

Use the PAPER TEXT only for:
- figure caption
- biological sample / cell line / tissue / organism
- treatment context
- abbreviation expansion

Do not use text to invent bands not visible in the image.
Use nearby page text, figure/caption context, and methods snippets to infer
biological_sample, cell_line_tissue, organism, and sample_type. Prefer the
caption/nearby page text over general methods text. If multiple samples are
plausible, list them separated by "; " and add a warning.
If unclear, use "".

If this is not a western blot/immunoblot/protein band panel, return:
{"is_western_blot": false, "reason": "not a western blot"}

If it is a western blot/immunoblot/protein band panel, return:
{
  "is_western_blot": true,
  "panel_label": null,
  "figure_label": "",
  "figure_caption": "",
  "biological_sample": "",
  "cell_line_tissue": "",
  "organism": "",
  "sample_type": "",
  "treatment_context": "",
  "targets_top_to_bottom": [
    {"row_index": 1, "target": "", "is_loading_control": false, "confidence": "low"}
  ],
  "lanes_left_to_right": [
    {"lane_index": 1, "condition": "", "confidence": "low"}
  ],
  "bands": [
    {"row_index": 1, "target": "", "lane_index": 1,
     "band_state": "present", "confidence": "low"}
  ],
  "warnings": []
}

If the crop contains multiple distinct blot panels or subpanels with different
lane layouts, return one object per panel in "panels" instead of forcing them
into one shared grid:
{
  "is_western_blot": true,
  "figure_label": "",
  "figure_caption": "",
  "biological_sample": "",
  "cell_line_tissue": "",
  "organism": "",
  "sample_type": "",
  "treatment_context": "",
  "panels": [
    {
      "panel_label": "E",
      "panel_title": "",
      "treatment_context": "",
      "targets_top_to_bottom": [
        {"row_index": 1, "target": "", "is_loading_control": false, "confidence": "low"}
      ],
      "lanes_left_to_right": [
        {"lane_index": 1, "condition": "", "confidence": "low"}
      ],
      "bands": [
        {"row_index": 1, "target": "", "lane_index": 1,
         "band_state": "present", "confidence": "low"}
      ],
      "warnings": []
    }
  ],
  "warnings": []
}

Rules:
- One band entry is required for every visible target row x every visible lane.
- band_state must be "present", "absent", or "uncertain".
- Use "uncertain" for faint, smeared, cropped, merged, or ambiguous bands.
- Do not estimate fold change or intensity.
- Read target names exactly as visible in the image.
- For multi-panel crops, preserve each panel label and extract every visible
  blot panel separately.
- If the image is a partial split from a larger crop, extract every complete
  visible blot panel and add a warning for rows, lane labels, or panels that
  are visibly cut off.
- Encode each lane condition with all visible time, dose, and treatment labels,
  e.g. "48 h; LY3214996 0.2 umol/L; 1 h".
- Loading controls include Actin, beta-actin, GAPDH, Tubulin, HSP90, Vinculin, and total protein.
- If lane labels are shown as + / - grids, encode condition as readable text,
  e.g. "Drug +; siRNA -".
""".strip()


NULLABLE_STRING = {"anyOf": [{"type": "string"}, {"type": "null"}]}
CONFIDENCE = {"enum": ["low", "medium", "high"]}
TARGET_SCHEMA = {
    "type": "object",
    "properties": {
        "row_index": {"type": "integer", "minimum": 1},
        "target": {"type": "string"},
        "is_loading_control": {"type": "boolean"},
        "confidence": CONFIDENCE,
    },
    "required": ["row_index", "target", "is_loading_control", "confidence"],
    "additionalProperties": False,
}
LANE_SCHEMA = {
    "type": "object",
    "properties": {
        "lane_index": {"type": "integer", "minimum": 1},
        "condition": {"type": "string"},
        "confidence": CONFIDENCE,
    },
    "required": ["lane_index", "condition", "confidence"],
    "additionalProperties": False,
}
BAND_SCHEMA = {
    "type": "object",
    "properties": {
        "row_index": {"type": "integer", "minimum": 1},
        "target": {"type": "string"},
        "lane_index": {"type": "integer", "minimum": 1},
        "band_state": {"enum": ["present", "absent", "uncertain"]},
        "confidence": CONFIDENCE,
    },
    "required": ["row_index", "target", "lane_index", "band_state", "confidence"],
    "additionalProperties": False,
}
PANEL_SCHEMA = {
    "type": "object",
    "properties": {
        "panel_label": NULLABLE_STRING,
        "panel_title": {"type": "string"},
        "treatment_context": {"type": "string"},
        "targets_top_to_bottom": {"type": "array", "items": TARGET_SCHEMA},
        "lanes_left_to_right": {"type": "array", "items": LANE_SCHEMA},
        "bands": {"type": "array", "items": BAND_SCHEMA},
        "warnings": {"type": "array", "items": {"type": "string"}},
    },
    "required": [
        "panel_label",
        "panel_title",
        "treatment_context",
        "targets_top_to_bottom",
        "lanes_left_to_right",
        "bands",
        "warnings",
    ],
    "additionalProperties": False,
}
EXTRACTION_SCHEMA = {
    "type": "object",
    "properties": {
        "is_western_blot": {"type": "boolean"},
        "reason": {"type": "string"},
        "panel_label": NULLABLE_STRING,
        "figure_label": {"type": "string"},
        "figure_caption": {"type": "string"},
        "biological_sample": {"type": "string"},
        "cell_line_tissue": {"type": "string"},
        "organism": {"type": "string"},
        "sample_type": {"type": "string"},
        "treatment_context": {"type": "string"},
        "targets_top_to_bottom": {"type": "array", "items": TARGET_SCHEMA},
        "lanes_left_to_right": {"type": "array", "items": LANE_SCHEMA},
        "bands": {"type": "array", "items": BAND_SCHEMA},
        "panels": {"type": "array", "items": PANEL_SCHEMA},
        "warnings": {"type": "array", "items": {"type": "string"}},
    },
    "required": ["is_western_blot"],
    "additionalProperties": False,
}


def parse_json(raw: str) -> Any:
    """Parse model JSON, tolerating code fences, thinking tags, and extra text."""
    raw = raw.strip()
    raw = raw.replace("```json", "").replace("```", "").strip()

    while "<think>" in raw and "</think>" in raw:
        start = raw.find("<think>")
        end = raw.find("</think>", start)
        raw = (raw[:start] + raw[end + len("</think>") :]).strip()

    try:
        return json.loads(raw)
    except json.JSONDecodeError:
        pass

    start = raw.find("{")
    end = raw.rfind("}")
    if start != -1 and end != -1 and end > start:
        try:
            return json.loads(raw[start : end + 1])
        except json.JSONDecodeError:
            pass

    return {"error": "bad_json", "raw": raw[:1200]}


class OpenAICompatibleVLM:
    """Small HTTP client for OpenAI-compatible vision chat completion APIs."""

    def __init__(
        self,
        base_url: str | None = None,
        api_key: str | None = None,
        model: str | None = None,
        timeout: int = DEFAULT_TIMEOUT,
        image_max_side: int = DEFAULT_IMAGE_MAX_SIDE,
    ) -> None:
        self.base_url = (
            base_url or os.environ.get("VLLM_BASE_URL", "") or "http://localhost:8000/v1"
        ).rstrip("/")
        self.api_key = api_key or os.environ.get("VLLM_API_KEY", "local")
        self.model = model or os.environ.get("VLLM_MODEL", "") or DEFAULT_VLM_MODEL
        self.timeout = timeout
        self.image_max_side = image_max_side

    def extract_candidate(
        self,
        candidate_path: str | Path,
        text_context: str,
        max_tokens: int = DEFAULT_MAX_TOKENS,
        image_max_side: int | None = None,
    ) -> Any:
        _, extraction = self.extract_candidate_with_raw(
            candidate_path,
            text_context,
            max_tokens=max_tokens,
            image_max_side=image_max_side,
        )
        return extraction

    def extract_candidate_with_raw(
        self,
        candidate_path: str | Path,
        text_context: str,
        max_tokens: int = DEFAULT_MAX_TOKENS,
        image_max_side: int | None = None,
    ) -> tuple[str, Any]:
        """Return exact response text alongside the permissively parsed legacy value."""

        try:
            import requests
        except ImportError as exc:  # pragma: no cover - depends on local env
            raise RuntimeError("requests is required for VLM extraction") from exc

        payload: dict[str, Any] = {
            "model": self.model,
            "messages": [
                {
                    "role": "user",
                    "content": [
                        {
                            "type": "image_url",
                            "image_url": {
                                "url": image_data_url(
                                    candidate_path,
                                    max_side=image_max_side or self.image_max_side,
                                )
                            },
                        },
                        {
                            "type": "text",
                            "text": f"{PROMPT}\n\n--- LOCAL TEXT ---\n{text_context}",
                        },
                    ],
                }
            ],
            "temperature": 0,
            "max_tokens": max_tokens,
            "chat_template_kwargs": {"enable_thinking": False},
            "response_format": {
                "type": "json_schema",
                "json_schema": {
                    "name": "western_blot_extraction",
                    "schema": EXTRACTION_SCHEMA,
                },
            },
        }
        response = requests.post(
            f"{self.base_url}/chat/completions",
            headers={
                "Authorization": f"Bearer {self.api_key}",
                "Content-Type": "application/json",
            },
            json=payload,
            timeout=self.timeout,
        )
        response.raise_for_status()
        data = response.json()
        raw = data["choices"][0]["message"]["content"]
        if not isinstance(raw, str):
            raise ValueError("vision model response content must be a string")
        extraction = parse_json(raw)
        if not isinstance(extraction, dict) or not isinstance(
            extraction.get("is_western_blot"), bool
        ):
            return raw, {"error": "invalid_extraction", "raw": raw[:1200]}
        return raw, extraction


def image_data_url(path: str | Path, max_side: int = DEFAULT_IMAGE_MAX_SIDE) -> str:
    """Return a resized crop as a PNG data URL."""
    img = pdf_preprocess.load_for_vlm(path, max_side=max_side)
    buffer = io.BytesIO()
    img.save(buffer, format="PNG")
    encoded = base64.b64encode(buffer.getvalue()).decode("ascii")
    return f"data:image/png;base64,{encoded}"


def run_vlm_extraction(
    run_dir: str | Path,
    base_url: str | None = None,
    api_key: str | None = None,
    model: str | None = None,
    max_tokens: int = DEFAULT_MAX_TOKENS,
    timeout: int = DEFAULT_TIMEOUT,
    image_max_side: int = DEFAULT_IMAGE_MAX_SIDE,
    limit: int | None = None,
    resume: bool = True,
    on_positive: Callable[[dict[str, Any]], int] | None = None,
) -> dict[str, Any]:
    """Run the configured VLM over LLM-threshold PDF candidates."""
    run_dir = Path(run_dir)
    candidates_path = run_dir / "llm_candidates.json"
    contexts_path = run_dir / "candidate_contexts.jsonl"
    output_jsonl = run_dir / "vlm_extractions.jsonl"
    output_json = run_dir / "vlm_extractions.json"
    positives_json = run_dir / "vlm_extractions_positive_only.json"
    failed_requests_jsonl = run_dir / "vlm_failed_requests.jsonl"

    candidates = json.loads(candidates_path.read_text(encoding="utf-8"))
    contexts = _read_contexts(contexts_path)
    if limit is not None:
        candidates = candidates[:limit]
    if not resume and failed_requests_jsonl.exists():
        failed_requests_jsonl.write_text("", encoding="utf-8")

    done_paths = set()
    results = []
    cached_results = 0
    if resume and output_jsonl.exists():
        existing_by_path = {}
        with output_jsonl.open(encoding="utf-8") as read_handle:
            for line in read_handle:
                if not line.strip():
                    continue
                record = json.loads(line)
                existing_by_path[record["candidate_path"]] = record

        for candidate in candidates:
            record = existing_by_path.get(candidate["candidate_path"])
            if not record:
                continue
            extraction = record.get("extraction")
            if _should_retry_extraction(extraction):
                continue
            results.append(record)
            done_paths.add(record["candidate_path"])
            cached_results += 1

        to_query = len(candidates) - len(done_paths)
        print(
            "VLM resume cache: "
            f"{cached_results}/{len(candidates)} candidates already have "
            "successful cached results; "
            f"{to_query} will query the VLM.",
            flush=True,
        )
        if to_query == 0:
            print(
                "VLM resume cache: no VLM requests needed for this run.",
                flush=True,
            )
    elif resume:
        print(
            "VLM resume cache: no existing extraction stream found; "
            f"{len(candidates)} will query the VLM.",
            flush=True,
        )
    else:
        print(
            f"VLM cache disabled: {len(candidates)} candidates will query the VLM.",
            flush=True,
        )

    extractor = OpenAICompatibleVLM(
        base_url=base_url,
        api_key=api_key,
        model=model,
        timeout=timeout,
        image_max_side=image_max_side,
    )

    def log_failure(
        candidate_path: Path,
        request_path: Path,
        stage: str,
        exc: Exception,
    ) -> None:
        _append_failed_request(
            failed_requests_jsonl,
            {
                "timestamp": datetime.now(UTC).isoformat(),
                "candidate_path": str(candidate_path),
                "request_path": str(request_path),
                "stage": stage,
                "model": extractor.model,
                "base_url": extractor.base_url,
                "timeout": extractor.timeout,
                "image_max_side": extractor.image_max_side,
                "max_tokens": max_tokens,
                **_exception_payload(exc),
            },
        )

    output_mode = "a" if resume else "w"
    streamed_positive_rows = 0
    queried_candidates = 0
    with output_jsonl.open(output_mode, encoding="utf-8") as stream_handle:
        for idx, candidate in enumerate(candidates, 1):
            candidate_path = candidate["candidate_path"]
            if candidate_path in done_paths:
                continue
            queried_candidates += 1
            context = contexts.get(candidate_path, "")
            print(
                f"{idx}/{len(candidates)} "
                f"score={candidate['cv_score']:.3f} "
                f"page={candidate['page']} "
                f"{candidate_path}",
                flush=True,
            )
            try:
                extraction = _extract_with_split_fallback(
                    extractor=extractor,
                    candidate_path=Path(candidate_path),
                    text_context=context,
                    max_tokens=max_tokens,
                    log_failure=log_failure,
                )
            except Exception as exc:
                extraction = {
                    "error": "vlm_request_failed",
                    "message": str(exc),
                }

            record = {**candidate, "extraction": extraction}
            results.append(record)
            stream_handle.write(json.dumps(record) + "\n")
            stream_handle.flush()
            if (
                on_positive is not None
                and isinstance(extraction, dict)
                and extraction.get("is_western_blot") is True
            ):
                streamed_positive_rows += on_positive(record)

    output_json.write_text(json.dumps(results, indent=2), encoding="utf-8")
    positives = [
        result
        for result in results
        if isinstance(result.get("extraction"), dict)
        and result["extraction"].get("is_western_blot") is True
    ]
    positives_json.write_text(json.dumps(positives, indent=2), encoding="utf-8")

    summary = {
        "run_dir": str(run_dir),
        "model": extractor.model,
        "image_max_side": extractor.image_max_side,
        "candidates": len(candidates),
        "results": len(results),
        "positive_results": len(positives),
        "cached_results": cached_results,
        "queried_candidates": queried_candidates,
        "streamed_positive_rows": streamed_positive_rows,
        "output_jsonl": str(output_jsonl),
        "output_json": str(output_json),
        "positives_json": str(positives_json),
        "failed_requests_jsonl": str(failed_requests_jsonl),
    }
    (run_dir / "vlm_summary.json").write_text(json.dumps(summary, indent=2), encoding="utf-8")
    return summary


def _extract_with_split_fallback(
    extractor: OpenAICompatibleVLM,
    candidate_path: Path,
    text_context: str,
    max_tokens: int,
    log_failure: Callable[[Path, Path, str, Exception], None] | None = None,
) -> Any:
    try:
        return extractor.extract_candidate(
            candidate_path=candidate_path,
            text_context=text_context,
            max_tokens=max_tokens,
        )
    except Exception as exc:
        if log_failure is not None:
            log_failure(candidate_path, candidate_path, "full", exc)
        split_paths = _split_large_candidate(candidate_path)
        if not split_paths:
            raise

        split_extractions = []
        split_errors = [str(exc)]
        for split_path in split_paths:
            try:
                split_extractions.append(
                    extractor.extract_candidate(
                        candidate_path=split_path,
                        text_context=text_context,
                        max_tokens=max_tokens,
                    )
                )
            except Exception as split_exc:
                if log_failure is not None:
                    log_failure(candidate_path, split_path, "split", split_exc)
                split_errors.append(str(split_exc))

        merged = _merge_split_extractions(split_extractions)
        if merged:
            warnings = merged.setdefault("warnings", [])
            warnings.append(
                "Candidate image was split into smaller crops after the full crop request failed."
            )
            merged["split_candidate_paths"] = [str(path) for path in split_paths]
            return merged

        raise RuntimeError("; ".join(split_errors)) from exc


def _split_large_candidate(candidate_path: Path) -> list[Path]:
    try:
        from PIL import Image
    except ImportError:
        return []

    with Image.open(candidate_path) as img:
        width, height = img.size
        if height < 1200 or width < 900:
            return []

        split_dir = candidate_path.parent.parent / "vlm_panel_splits"
        split_dir.mkdir(parents=True, exist_ok=True)
        overlap = min(120, max(40, height // 14))
        midpoint = height // 2
        split_y = min(height - 200, max(200, midpoint + overlap))
        boxes = [
            (0, 0, width, split_y),
            (0, split_y, width, height),
        ]
        paths = []
        for idx, box in enumerate(boxes, 1):
            split_path = split_dir / f"{candidate_path.stem}_part_{idx:02d}.png"
            img.crop(box).save(split_path)
            paths.append(split_path)
        return paths


def _merge_split_extractions(extractions: list[Any]) -> dict[str, Any] | None:
    panels: list[dict[str, Any]] = []
    root: dict[str, Any] = {
        "is_western_blot": True,
        "figure_label": "",
        "figure_caption": "",
        "biological_sample": "",
        "cell_line_tissue": "",
        "organism": "",
        "sample_type": "",
        "treatment_context": "",
        "panels": panels,
        "warnings": [],
    }

    for extraction in extractions:
        if not isinstance(extraction, dict) or extraction.get("is_western_blot") is not True:
            continue

        for key in (
            "figure_label",
            "figure_caption",
            "biological_sample",
            "cell_line_tissue",
            "organism",
            "sample_type",
            "treatment_context",
        ):
            if not root.get(key) and extraction.get(key):
                root[key] = extraction[key]

        if isinstance(extraction.get("warnings"), list):
            root["warnings"].extend(extraction["warnings"])

        split_panels = extraction.get("panels")
        if isinstance(split_panels, list) and split_panels:
            panels.extend(panel for panel in split_panels if isinstance(panel, dict))
        else:
            panel = {
                key: value
                for key, value in extraction.items()
                if key
                in {
                    "panel_label",
                    "panel_title",
                    "treatment_context",
                    "targets_top_to_bottom",
                    "lanes_left_to_right",
                    "bands",
                    "warnings",
                }
            }
            panels.append(panel)

    return root if panels else None


def _append_failed_request(path: Path, payload: dict[str, Any]) -> None:
    with path.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(payload) + "\n")


def _exception_payload(exc: Exception) -> dict[str, Any]:
    payload: dict[str, Any] = {
        "error_type": type(exc).__name__,
        "error_message": str(exc),
    }
    response = getattr(exc, "response", None)
    if response is not None:
        payload["status_code"] = getattr(response, "status_code", None)
        payload["response_url"] = getattr(response, "url", None)
        text = getattr(response, "text", None)
        if text:
            payload["response_text"] = text[:1200]
    return payload


def _should_retry_extraction(extraction: Any) -> bool:
    if not isinstance(extraction, dict):
        return True
    return bool(extraction.get("error"))


def _read_contexts(path: Path) -> dict[str, str]:
    contexts = {}
    with path.open(encoding="utf-8") as handle:
        for line in handle:
            if not line.strip():
                continue
            record = json.loads(line)
            contexts[record["candidate_path"]] = record["text_context"]
    return contexts
