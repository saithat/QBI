"""Configurable extraction implementation boundary and retained local adapter."""

from __future__ import annotations

import hashlib
import io
import json
import tempfile
import time
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Protocol, cast
from uuid import NAMESPACE_URL, UUID, uuid5

from hiveblot_contracts import (
    BoundingRegion,
    ModelIdentifier,
    PipelineIdentifier,
    ToolIdentifier,
    WesternBlotExtractionImplementation,
    WesternBlotExtractionInput,
    WesternBlotFigureCandidate,
    WesternBlotFigureCandidateSet,
    WesternBlotSourceKind,
)

from hiveblot import pdf_preprocess
from hiveblot.settings import Settings
from hiveblot.vlm_extract import PROMPT, OpenAICompatibleVLM

from .errors import ExtractionImplementationNotFound, UnsupportedExtractionSource


@dataclass(frozen=True, slots=True)
class DetectionExecution:
    raw_output_json: str
    result: WesternBlotFigureCandidateSet
    latency_ms: int


@dataclass(frozen=True, slots=True)
class RawCandidateModelResponse:
    candidate_id: UUID
    raw_output: str
    latency_ms: int
    cost_microusd: int | None = None


@dataclass(frozen=True, slots=True)
class ModelExecution:
    responses: tuple[RawCandidateModelResponse, ...]

    @property
    def latency_ms(self) -> int:
        return sum(item.latency_ms for item in self.responses)

    @property
    def cost_microusd(self) -> int:
        return sum(item.cost_microusd or 0 for item in self.responses)

    @property
    def cost_is_complete(self) -> bool:
        return all(item.cost_microusd is not None for item in self.responses)


class WesternBlotExtractionImplementationAdapter(Protocol):
    @property
    def identity(self) -> WesternBlotExtractionImplementation: ...

    def detect(
        self,
        extraction_input: WesternBlotExtractionInput,
        content: bytes,
    ) -> DetectionExecution: ...

    def infer(
        self,
        extraction_input: WesternBlotExtractionInput,
        content: bytes,
        candidates: WesternBlotFigureCandidateSet,
    ) -> ModelExecution: ...


class ExtractionImplementationRegistry:
    def __init__(
        self,
        implementations: Mapping[str, WesternBlotExtractionImplementationAdapter],
    ) -> None:
        self._implementations = dict(implementations)
        if any(
            name != value.identity.implementation_name for name, value in implementations.items()
        ):
            raise ValueError("extraction registry keys must match implementation identities")

    def get(self, name: str) -> WesternBlotExtractionImplementationAdapter:
        try:
            return self._implementations[name]
        except KeyError as exc:
            raise ExtractionImplementationNotFound(
                f"western-blot extraction implementation {name!r} is not configured"
            ) from exc

    def list(self) -> tuple[WesternBlotExtractionImplementation, ...]:
        return tuple(
            sorted(
                (item.identity for item in self._implementations.values()),
                key=lambda item: (item.implementation_name, item.implementation_version),
            )
        )


class LegacyVlmExtractionImplementation:
    """Wrap the retained OpenCV/Qwen behavior behind versioned stage contracts."""

    def __init__(
        self,
        settings: Settings,
        *,
        client: OpenAICompatibleVLM | None = None,
    ) -> None:
        prompt_version = f"sha256:{hashlib.sha256(PROMPT.encode()).hexdigest()}"
        detector = ToolIdentifier(name="legacy-opencv-figure-detector", version="1.0.0")
        model = ModelIdentifier(
            provider="vllm",
            name=settings.vllm_model,
            version=settings.vllm_model_revision,
        )
        assembler = ToolIdentifier(name="hiveblot-western-blot-assembler", version="1.0.0")
        fingerprint = hashlib.sha256(
            json.dumps(
                {
                    "implementation": "1.0.0",
                    "detector": detector.model_dump(mode="json"),
                    "model": model.model_dump(mode="json"),
                    "prompt_version": prompt_version,
                    "assembler": assembler.model_dump(mode="json"),
                },
                sort_keys=True,
                separators=(",", ":"),
            ).encode()
        ).hexdigest()[:16]
        self._identity = WesternBlotExtractionImplementation(
            implementation_name="legacy-local-vlm",
            implementation_version="1.0.0",
            pipeline=PipelineIdentifier(
                name="western-blot-extraction-legacy-local-vlm",
                version=f"1.0.0-{fingerprint}",
            ),
            detector=detector,
            model=model,
            prompt_version=prompt_version,
            assembler=assembler,
        )
        self._client = client or OpenAICompatibleVLM(
            base_url=settings.vllm_base_url,
            api_key=settings.vllm_api_key.get_secret_value(),
            model=settings.vllm_model,
            timeout=int(settings.vllm_timeout_seconds),
            image_max_side=settings.vllm_image_max_side,
        )

    @property
    def identity(self) -> WesternBlotExtractionImplementation:
        return self._identity

    def detect(
        self,
        extraction_input: WesternBlotExtractionInput,
        content: bytes,
    ) -> DetectionExecution:
        started = time.perf_counter()
        if extraction_input.source_kind is WesternBlotSourceKind.IMAGE:
            result, raw = self._detect_image(extraction_input, content)
        elif extraction_input.source_kind is WesternBlotSourceKind.PDF:
            result, raw = self._detect_pdf(extraction_input, content)
        else:  # pragma: no cover - strict enum protects this branch
            raise UnsupportedExtractionSource(
                f"unsupported extraction source kind {extraction_input.source_kind.value}"
            )
        return DetectionExecution(
            raw_output_json=json.dumps(raw, sort_keys=True, separators=(",", ":")),
            result=result,
            latency_ms=_elapsed_ms(started),
        )

    def infer(
        self,
        extraction_input: WesternBlotExtractionInput,
        content: bytes,
        candidates: WesternBlotFigureCandidateSet,
    ) -> ModelExecution:
        if extraction_input.source_kind is WesternBlotSourceKind.IMAGE:
            crops = _image_crops(content, candidates)
        elif extraction_input.source_kind is WesternBlotSourceKind.PDF:
            crops = _pdf_crops(content, candidates, dpi=extraction_input.configuration.dpi)
        else:  # pragma: no cover - strict enum protects this branch
            raise UnsupportedExtractionSource(
                f"unsupported extraction source kind {extraction_input.source_kind.value}"
            )
        responses: list[RawCandidateModelResponse] = []
        with tempfile.TemporaryDirectory(prefix="hiveblot-vlm-") as directory:
            for candidate, crop_bytes, text_context in crops:
                path = Path(directory, f"{candidate.candidate_id}.png")
                path.write_bytes(crop_bytes)
                started = time.perf_counter()
                raw_output, _ = self._client.extract_candidate_with_raw(
                    path,
                    text_context,
                    max_tokens=extraction_input.configuration.model_max_tokens,
                    image_max_side=extraction_input.configuration.image_max_side,
                )
                responses.append(
                    RawCandidateModelResponse(
                        candidate_id=candidate.candidate_id,
                        raw_output=raw_output,
                        latency_ms=_elapsed_ms(started),
                    )
                )
        return ModelExecution(responses=tuple(responses))

    def _detect_image(
        self,
        extraction_input: WesternBlotExtractionInput,
        content: bytes,
    ) -> tuple[WesternBlotFigureCandidateSet, dict[str, object]]:
        try:
            from PIL import Image
        except ImportError as exc:  # pragma: no cover - package dependency
            raise RuntimeError("Pillow is required for stored-image extraction") from exc
        with Image.open(io.BytesIO(content)) as image:
            width, height = image.size
        candidate_id = _candidate_id(
            extraction_input.source_artifact.sha256,
            page_number=None,
            bbox=(0, 0, width, height),
            detector_version=self.identity.detector.version,
        )
        region = BoundingRegion(
            region_id=_stable_id(candidate_id, "detected-region"),
            source_artifact_id=extraction_input.source_artifact.artifact_id,
            x=0.0,
            y=0.0,
            width=float(width),
            height=float(height),
            canvas_width=width,
            canvas_height=height,
        )
        candidate = WesternBlotFigureCandidate(
            candidate_id=candidate_id,
            source_artifact=extraction_input.source_artifact,
            region=region,
            tight_region=region,
            detector_score=1.0,
        )
        result = WesternBlotFigureCandidateSet(
            source_artifact=extraction_input.source_artifact,
            source_kind=extraction_input.source_kind,
            detector=self.identity.detector,
            candidates=(candidate,),
        )
        return result, {
            "adapter": "stored-image-full-frame",
            "width": width,
            "height": height,
            "candidate_id": str(candidate_id),
        }

    def _detect_pdf(
        self,
        extraction_input: WesternBlotExtractionInput,
        content: bytes,
    ) -> tuple[WesternBlotFigureCandidateSet, dict[str, object]]:
        configuration = extraction_input.configuration
        with tempfile.TemporaryDirectory(prefix="hiveblot-pdf-detect-") as directory:
            root = Path(directory)
            pdf_path = root / "source.pdf"
            pdf_path.write_bytes(content)
            pages = pdf_preprocess.render_pdf(pdf_path, root / "pages", dpi=configuration.dpi)
            detected = pdf_preprocess.generate_candidates(
                pages,
                root / "candidates",
                paper_id=extraction_input.source_artifact.sha256,
                min_score=configuration.minimum_candidate_score,
            )
            selected = pdf_preprocess.filter_candidates_for_llm(
                detected,
                min_score=configuration.minimum_model_score,
            )[: configuration.maximum_candidates]
            page_by_number = {int(page["page"]): page for page in pages}
            candidates = tuple(
                _candidate_from_legacy(
                    extraction_input,
                    record,
                    page_by_number[int(record["page"])],
                    detector_version=self.identity.detector.version,
                )
                for record in selected
            )
            raw_candidates = [
                {
                    "page": int(record["page"]),
                    "bbox_page": record["bbox_page"],
                    "tight_bbox_page": record["tight_bbox_page"],
                    "cv_score": float(record["cv_score"]),
                    "candidate_id": str(candidate.candidate_id),
                }
                for record, candidate in zip(selected, candidates, strict=True)
            ]
        return (
            WesternBlotFigureCandidateSet(
                source_artifact=extraction_input.source_artifact,
                source_kind=extraction_input.source_kind,
                detector=self.identity.detector,
                candidates=candidates,
            ),
            {
                "adapter": "retained-pdf-opencv",
                "dpi": configuration.dpi,
                "detected_count": len(detected),
                "selected_count": len(candidates),
                "candidates": raw_candidates,
            },
        )


def _candidate_from_legacy(
    extraction_input: WesternBlotExtractionInput,
    record: dict[str, object],
    page: dict[str, object],
    *,
    detector_version: str,
) -> WesternBlotFigureCandidate:
    page_number = _integer(record["page"], "legacy candidate page")
    bbox = _bbox(record["bbox_page"])
    tight_bbox = _bbox(record["tight_bbox_page"])
    canvas_width = _integer(page["width_px"], "rendered page width")
    canvas_height = _integer(page["height_px"], "rendered page height")
    candidate_id = _candidate_id(
        extraction_input.source_artifact.sha256,
        page_number=page_number,
        bbox=bbox,
        detector_version=detector_version,
    )
    return WesternBlotFigureCandidate(
        candidate_id=candidate_id,
        source_artifact=extraction_input.source_artifact,
        region=_bounding_region(
            _stable_id(candidate_id, "detected-region"),
            extraction_input.source_artifact.artifact_id,
            bbox,
            canvas_width,
            canvas_height,
            page_number,
        ),
        tight_region=_bounding_region(
            _stable_id(candidate_id, "tight-region"),
            extraction_input.source_artifact.artifact_id,
            tight_bbox,
            canvas_width,
            canvas_height,
            page_number,
        ),
        detector_score=_number(record["cv_score"], "legacy candidate score"),
    )


def _image_crops(
    content: bytes,
    candidates: WesternBlotFigureCandidateSet,
) -> tuple[tuple[WesternBlotFigureCandidate, bytes, str], ...]:
    try:
        from PIL import Image
    except ImportError as exc:  # pragma: no cover - package dependency
        raise RuntimeError("Pillow is required for stored-image extraction") from exc
    with Image.open(io.BytesIO(content)) as source:
        image = source.convert("RGB")
        result = []
        for candidate in candidates.candidates:
            region = candidate.region
            crop = image.crop(
                (
                    int(region.x),
                    int(region.y),
                    int(region.x + region.width),
                    int(region.y + region.height),
                )
            )
            buffer = io.BytesIO()
            crop.save(buffer, format="PNG")
            result.append((candidate, buffer.getvalue(), ""))
    return tuple(result)


def _pdf_crops(
    content: bytes,
    candidates: WesternBlotFigureCandidateSet,
    *,
    dpi: int,
) -> tuple[tuple[WesternBlotFigureCandidate, bytes, str], ...]:
    try:
        import fitz
        from PIL import Image
    except ImportError as exc:  # pragma: no cover - package dependencies
        raise RuntimeError("PyMuPDF and Pillow are required for PDF extraction") from exc
    scale = dpi / 72
    result = []
    with fitz.open(stream=content, filetype="pdf") as document:
        page_records = [
            {
                "page": index + 1,
                "text": page.get_text(),
            }
            for index, page in enumerate(document)
        ]
        paper_text = pdf_preprocess.full_paper_text(page_records)
        for candidate in candidates.candidates:
            page_number = candidate.region.page_number
            if page_number is None:
                raise UnsupportedExtractionSource("PDF candidates require a page number")
            page = document[page_number - 1]
            pixmap = page.get_pixmap(matrix=fitz.Matrix(scale, scale), alpha=False)
            with Image.open(io.BytesIO(pixmap.tobytes("png"))) as rendered:
                region = candidate.region
                crop = rendered.crop(
                    (
                        int(region.x),
                        int(region.y),
                        int(region.x + region.width),
                        int(region.y + region.height),
                    )
                ).convert("RGB")
                buffer = io.BytesIO()
                crop.save(buffer, format="PNG")
            text_context = pdf_preprocess.build_text_context_for_candidate(
                {
                    "paper_id": candidates.source_artifact.sha256,
                    "page": page_number,
                    "bbox_page": [
                        region.x,
                        region.y,
                        region.x + region.width,
                        region.y + region.height,
                    ],
                },
                page_records,
                paper_text,
            )
            result.append((candidate, buffer.getvalue(), text_context))
    return tuple(result)


def _bbox(value: object) -> tuple[int, int, int, int]:
    if not isinstance(value, list) or len(value) != 4:
        raise ValueError("legacy candidate bounding boxes require four coordinates")
    return cast(tuple[int, int, int, int], tuple(int(item) for item in value))


def _integer(value: object, label: str) -> int:
    if isinstance(value, bool) or not isinstance(value, (int, float, str)):
        raise ValueError(f"{label} must be numeric")
    return int(value)


def _number(value: object, label: str) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float, str)):
        raise ValueError(f"{label} must be numeric")
    return float(value)


def _bounding_region(
    region_id: UUID,
    artifact_id: UUID,
    bbox: tuple[int, int, int, int],
    canvas_width: int,
    canvas_height: int,
    page_number: int | None,
) -> BoundingRegion:
    x0, y0, x1, y1 = bbox
    return BoundingRegion(
        region_id=region_id,
        source_artifact_id=artifact_id,
        x=float(x0),
        y=float(y0),
        width=float(x1 - x0),
        height=float(y1 - y0),
        canvas_width=canvas_width,
        canvas_height=canvas_height,
        page_number=page_number,
    )


def _candidate_id(
    source_sha256: str,
    *,
    page_number: int | None,
    bbox: tuple[int, int, int, int],
    detector_version: str,
) -> UUID:
    return uuid5(
        NAMESPACE_URL,
        f"urn:hiveblot:figure-candidate:{source_sha256}:{page_number}:{bbox}:{detector_version}",
    )


def _stable_id(parent_id: UUID, key: str) -> UUID:
    return uuid5(NAMESPACE_URL, f"urn:hiveblot:western-blot:{parent_id}:{key}")


def _elapsed_ms(started: float) -> int:
    return max(0, round((time.perf_counter() - started) * 1000))
