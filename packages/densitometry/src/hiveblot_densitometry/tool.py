"""Deterministic integrated-darkness measurement and quality control."""

from __future__ import annotations

import hashlib
import io
import json
import math
from dataclasses import dataclass
from typing import Any
from uuid import NAMESPACE_URL, UUID, uuid5

import numpy as np
from hiveblot_contracts import (
    ArtifactReference,
    BoundingRegion,
    DensitometryBackgroundMethod,
    DensitometryBandRegion,
    DensitometryImageKind,
    DensitometryInput,
    DensitometryLaneRegion,
    DensitometryMeasurement,
    DensitometryNormalizationMethod,
    DensitometryQcCode,
    DensitometryQcFlag,
    DensitometryQcSeverity,
    DensitometryReproducibility,
    DensitometryResult,
    DensitometrySuitability,
    DensitometryTargetRegion,
    ToolIdentifier,
)
from PIL import Image, ImageDraw
from PIL import __version__ as pillow_version

from .errors import InvalidDensitometryInput

ALGORITHM_NAME = "integrated-darkness-background-subtraction-v1"
NUMERICAL_PRECISION_DECIMAL_PLACES = 6
DENSITOMETRY_TOOL = ToolIdentifier(name="hiveblot-densitometry", version="1.0.0")


@dataclass(frozen=True, slots=True)
class DensitometryComputation:
    densitometry_input: DensitometryInput
    measurements: tuple[DensitometryMeasurement, ...]
    global_qc_flags: tuple[DensitometryQcFlag, ...]
    suitability: DensitometrySuitability
    overlay_png: bytes
    raw_output_json: str
    input_sha256: str
    configuration_sha256: str

    def result(self, overlay_artifact: ArtifactReference) -> DensitometryResult:
        overlay_sha256 = hashlib.sha256(self.overlay_png).hexdigest()
        if (
            overlay_artifact.sha256 != overlay_sha256
            or overlay_artifact.byte_size != len(self.overlay_png)
            or overlay_artifact.media_type != "image/png"
        ):
            raise InvalidDensitometryInput(
                "published overlay artifact does not match deterministic overlay bytes"
            )
        result_id = uuid5(
            NAMESPACE_URL,
            f"urn:hiveblot:densitometry-result:{DENSITOMETRY_TOOL.version}:"
            f"{self.input_sha256}:{overlay_sha256}",
        )
        return DensitometryResult(
            result_id=result_id,
            input=self.densitometry_input,
            tool=DENSITOMETRY_TOOL,
            suitability=self.suitability,
            measurements=self.measurements,
            global_qc_flags=self.global_qc_flags,
            analysis_overlay=overlay_artifact,
            reproducibility=DensitometryReproducibility(
                algorithm=ALGORITHM_NAME,
                tool=DENSITOMETRY_TOOL,
                input_sha256=self.input_sha256,
                configuration_sha256=self.configuration_sha256,
                source_image_sha256=self.densitometry_input.image_artifact.sha256,
                numerical_precision_decimal_places=NUMERICAL_PRECISION_DECIMAL_PLACES,
                library_versions=(
                    ToolIdentifier(name="numpy", version=np.__version__),
                    ToolIdentifier(name="pillow", version=pillow_version),
                ),
            ),
        )


class DeterministicDensitometryTool:
    @property
    def identity(self) -> ToolIdentifier:
        return DENSITOMETRY_TOOL

    def analyze(
        self,
        densitometry_input: DensitometryInput,
        image_bytes: bytes,
    ) -> DensitometryComputation:
        source_sha256 = hashlib.sha256(image_bytes).hexdigest()
        if source_sha256 != densitometry_input.image_artifact.sha256:
            raise InvalidDensitometryInput("source image bytes do not match the input SHA-256")
        input_json = _canonical_json(densitometry_input)
        input_sha256 = hashlib.sha256(input_json.encode()).hexdigest()
        configuration_json = _canonical_json(densitometry_input.configuration)
        configuration_sha256 = hashlib.sha256(configuration_json.encode()).hexdigest()
        image, grayscale = _decode_image(densitometry_input, image_bytes)
        global_flags = _global_flags(densitometry_input, input_sha256)
        raw_records: list[dict[str, Any]] = []
        measurements: list[DensitometryMeasurement] = []
        lanes = {item.lane_id: item for item in densitometry_input.lanes}
        targets = {item.target_id: item for item in densitometry_input.targets}

        for band in sorted(
            densitometry_input.bands,
            key=lambda item: (lanes[item.lane_id].lane_index, str(item.target_id)),
        ):
            lane = lanes[band.lane_id]
            target = targets[band.target_id]
            measurement, raw = _measure_band(
                densitometry_input,
                grayscale,
                input_sha256=input_sha256,
                lane=lane,
                target=target,
                band=band,
            )
            measurements.append(measurement)
            raw_records.append(raw)

        normalized, normalization_flags = _normalize(
            densitometry_input,
            tuple(measurements),
            input_sha256=input_sha256,
        )
        global_flags.extend(normalization_flags)
        suitability = _suitability(densitometry_input, normalized, tuple(global_flags))
        overlay_png = _render_overlay(densitometry_input, image)
        raw_output_json = json.dumps(
            {
                "schema_version": "1.0",
                "algorithm": ALGORITHM_NAME,
                "tool": DENSITOMETRY_TOOL.model_dump(mode="json"),
                "input_sha256": input_sha256,
                "configuration_sha256": configuration_sha256,
                "source_image_sha256": source_sha256,
                "measurements": raw_records,
                "global_qc_flags": [item.model_dump(mode="json") for item in global_flags],
                "suitability": suitability.value,
            },
            sort_keys=True,
            separators=(",", ":"),
            ensure_ascii=False,
        )
        return DensitometryComputation(
            densitometry_input=densitometry_input,
            measurements=normalized,
            global_qc_flags=tuple(global_flags),
            suitability=suitability,
            overlay_png=overlay_png,
            raw_output_json=raw_output_json,
            input_sha256=input_sha256,
            configuration_sha256=configuration_sha256,
        )


def _decode_image(
    densitometry_input: DensitometryInput,
    image_bytes: bytes,
) -> tuple[Image.Image, np.ndarray[Any, np.dtype[np.uint8]]]:
    try:
        with Image.open(io.BytesIO(image_bytes)) as source:
            source.seek(0)
            image = source.convert("RGB")
    except (OSError, ValueError) as exc:
        raise InvalidDensitometryInput("source artifact is not a readable raster image") from exc
    expected = (
        densitometry_input.lanes[0].region.canvas_width,
        densitometry_input.lanes[0].region.canvas_height,
    )
    if image.size != expected:
        raise InvalidDensitometryInput(
            f"image dimensions {image.size} do not match geometry canvas {expected}"
        )
    grayscale = np.asarray(image.convert("L"), dtype=np.uint8)
    return image, grayscale


def _measure_band(
    densitometry_input: DensitometryInput,
    grayscale: np.ndarray[Any, np.dtype[np.uint8]],
    *,
    input_sha256: str,
    lane: DensitometryLaneRegion,
    target: DensitometryTargetRegion,
    band: DensitometryBandRegion,
) -> tuple[DensitometryMeasurement, dict[str, Any]]:
    bounds = _pixel_bounds(band.region)
    x0, y0, x1, y1 = bounds
    pixels = grayscale[y0:y1, x0:x1]
    if pixels.size == 0:  # pragma: no cover - contract and bounds protect this
        raise InvalidDensitometryInput(f"band {band.band_id} resolves to no image pixels")
    darkness = 255.0 - pixels.astype(np.float64)
    raw_intensity = float(np.sum(darkness, dtype=np.float64))
    background, sample_count, background_cv, fallback = _background(
        densitometry_input,
        grayscale,
        lane.region,
        target.region,
        band.region,
    )
    background_estimate = background * float(pixels.size)
    corrected = max(0.0, raw_intensity - background_estimate)
    flags: list[DensitometryQcFlag] = []
    width = x1 - x0
    height = y1 - y0
    configuration = densitometry_input.configuration
    if (
        width < configuration.minimum_band_width_pixels
        or height < configuration.minimum_band_height_pixels
        or pixels.size < configuration.minimum_band_area_pixels
    ):
        flags.append(
            _flag(
                input_sha256,
                DensitometryQcCode.INSUFFICIENT_RESOLUTION,
                DensitometryQcSeverity.ERROR,
                "Band region is below the configured minimum pixel dimensions.",
                lane_id=lane.lane_id,
                target_id=target.target_id,
                band_id=band.band_id,
                metric_value=float(pixels.size),
            )
        )
    saturated_fraction = float(
        np.count_nonzero(pixels <= configuration.saturation_black_level) / pixels.size
    )
    if saturated_fraction >= configuration.saturation_fraction_threshold:
        flags.append(
            _flag(
                input_sha256,
                DensitometryQcCode.SATURATION,
                DensitometryQcSeverity.ERROR,
                "Dark-pixel saturation exceeds the configured fraction threshold.",
                lane_id=lane.lane_id,
                target_id=target.target_id,
                band_id=band.band_id,
                metric_value=_rounded(saturated_fraction),
            )
        )
    if fallback:
        flags.append(
            _flag(
                input_sha256,
                DensitometryQcCode.EMPTY_BACKGROUND,
                DensitometryQcSeverity.WARNING,
                "The configured background region contained no pixels; the complete "
                "lane/target cell was used.",
                lane_id=lane.lane_id,
                target_id=target.target_id,
                band_id=band.band_id,
            )
        )
    if background_cv is not None and (
        background_cv >= configuration.uneven_background_cv_threshold
    ):
        flags.append(
            _flag(
                input_sha256,
                DensitometryQcCode.UNEVEN_BACKGROUND,
                DensitometryQcSeverity.WARNING,
                "Background variation exceeds the configured coefficient-of-variation threshold.",
                lane_id=lane.lane_id,
                target_id=target.target_id,
                band_id=band.band_id,
                metric_value=_rounded(background_cv),
            )
        )
    measurement = DensitometryMeasurement(
        measurement_id=_stable_id(input_sha256, f"measurement:{band.band_id}"),
        lane_id=lane.lane_id,
        lane_index=lane.lane_index,
        lane_label=lane.label,
        target_id=target.target_id,
        target_label=target.label,
        band_id=band.band_id,
        pixel_count=int(pixels.size),
        raw_intensity=_rounded(raw_intensity),
        background_per_pixel=_rounded(background),
        background_estimate=_rounded(background_estimate),
        corrected_intensity=_rounded(corrected),
        normalized_intensity=None,
        qc_flags=tuple(flags),
    )
    return measurement, {
        "measurement_id": str(measurement.measurement_id),
        "lane_id": str(lane.lane_id),
        "target_id": str(target.target_id),
        "band_id": str(band.band_id),
        "pixel_bounds": bounds,
        "pixel_count": int(pixels.size),
        "darkness_sum": _rounded(raw_intensity),
        "background_sample_count": sample_count,
        "background_per_pixel": _rounded(background),
        "background_cv": _rounded(background_cv) if background_cv is not None else None,
        "local_border_fallback": fallback,
        "corrected_intensity": _rounded(corrected),
        "saturated_fraction": _rounded(saturated_fraction),
    }


def _background(
    densitometry_input: DensitometryInput,
    grayscale: np.ndarray[Any, np.dtype[np.uint8]],
    lane_region: BoundingRegion,
    target_region: BoundingRegion,
    band_region: BoundingRegion,
) -> tuple[float, int, float | None, bool]:
    configuration = densitometry_input.configuration
    if configuration.background_method is DensitometryBackgroundMethod.NONE:
        return 0.0, 0, None, False
    cell_bounds = _intersection_bounds(lane_region, target_region)
    cx0, cy0, cx1, cy1 = cell_bounds
    cell = 255.0 - grayscale[cy0:cy1, cx0:cx1].astype(np.float64)
    bx0, by0, bx1, by1 = _pixel_bounds(band_region)
    fallback = False
    if configuration.background_method is DensitometryBackgroundMethod.LOCAL_BORDER:
        margin = configuration.local_border_pixels
        ex0, ey0 = max(cx0, bx0 - margin), max(cy0, by0 - margin)
        ex1, ey1 = min(cx1, bx1 + margin), min(cy1, by1 + margin)
        expanded = 255.0 - grayscale[ey0:ey1, ex0:ex1].astype(np.float64)
        mask = np.ones(expanded.shape, dtype=bool)
        mask[by0 - ey0 : by1 - ey0, bx0 - ex0 : bx1 - ex0] = False
        samples = expanded[mask]
        if samples.size == 0:
            samples = cell.reshape(-1)
            fallback = True
    else:
        mask = np.ones(cell.shape, dtype=bool)
        mask[by0 - cy0 : by1 - cy0, bx0 - cx0 : bx1 - cx0] = False
        samples = cell[mask]
        if samples.size == 0:
            samples = cell.reshape(-1)
            fallback = True
    percentile = float(np.percentile(samples, configuration.background_percentile, method="linear"))
    mean = float(np.mean(samples, dtype=np.float64))
    standard_deviation = float(np.std(samples, dtype=np.float64))
    coefficient = 0.0 if mean == 0 else standard_deviation / mean
    return percentile, int(samples.size), coefficient, fallback


def _normalize(
    densitometry_input: DensitometryInput,
    measurements: tuple[DensitometryMeasurement, ...],
    *,
    input_sha256: str,
) -> tuple[tuple[DensitometryMeasurement, ...], list[DensitometryQcFlag]]:
    if (
        densitometry_input.configuration.normalization_method
        is DensitometryNormalizationMethod.NONE
    ):
        return (
            tuple(_with_normalized(item, item.corrected_intensity) for item in measurements),
            [],
        )
    control_id = densitometry_input.loading_control_target_id
    if control_id is None:
        return measurements, [
            _flag(
                input_sha256,
                DensitometryQcCode.MISSING_LOADING_CONTROL,
                DensitometryQcSeverity.ERROR,
                "Loading-control normalization was requested without a loading-control target.",
            )
        ]
    controls = {
        item.lane_id: item.corrected_intensity
        for item in measurements
        if item.target_id == control_id
    }
    flags: list[DensitometryQcFlag] = []
    zero_lanes = {lane_id for lane_id, value in controls.items() if value <= 0}
    for lane_id in sorted(zero_lanes, key=str):
        flags.append(
            _flag(
                input_sha256,
                DensitometryQcCode.ZERO_LOADING_CONTROL,
                DensitometryQcSeverity.ERROR,
                "Loading-control corrected intensity is zero for this lane.",
                lane_id=lane_id,
                target_id=control_id,
            )
        )
    return (
        tuple(
            _with_normalized(
                item,
                (
                    None
                    if item.lane_id in zero_lanes
                    else _rounded(item.corrected_intensity / controls[item.lane_id])
                ),
            )
            for item in measurements
        ),
        flags,
    )


def _global_flags(
    densitometry_input: DensitometryInput,
    input_sha256: str,
) -> list[DensitometryQcFlag]:
    result: list[DensitometryQcFlag] = []
    if densitometry_input.image_kind is DensitometryImageKind.PUBLICATION_FIGURE:
        result.append(
            _flag(
                input_sha256,
                DensitometryQcCode.PUBLICATION_FIGURE_SOURCE,
                DensitometryQcSeverity.WARNING,
                "Measurements use a publication figure and are exploratory only.",
            )
        )
    if not densitometry_input.configuration.exposure_known:
        result.append(
            _flag(
                input_sha256,
                DensitometryQcCode.UNKNOWN_EXPOSURE,
                DensitometryQcSeverity.WARNING,
                "Image exposure is unknown.",
            )
        )
    if not densitometry_input.configuration.lane_boundaries_reviewed:
        result.append(
            _flag(
                input_sha256,
                DensitometryQcCode.UNCLEAR_LANE_BOUNDARIES,
                DensitometryQcSeverity.WARNING,
                "Lane boundaries have not been explicitly reviewer-validated.",
            )
        )
    return result


def _suitability(
    densitometry_input: DensitometryInput,
    measurements: tuple[DensitometryMeasurement, ...],
    global_flags: tuple[DensitometryQcFlag, ...],
) -> DensitometrySuitability:
    flags = (*global_flags, *(flag for item in measurements for flag in item.qc_flags))
    if any(flag.severity is DensitometryQcSeverity.ERROR for flag in flags):
        return DensitometrySuitability.NOT_ANALYZABLE
    if densitometry_input.image_kind is DensitometryImageKind.PUBLICATION_FIGURE:
        return DensitometrySuitability.EXPLORATORY_ONLY
    if flags:
        return DensitometrySuitability.SEMI_QUANTITATIVE
    return DensitometrySuitability.QUANTITATIVE


def _render_overlay(densitometry_input: DensitometryInput, image: Image.Image) -> bytes:
    overlay = image.copy()
    draw = ImageDraw.Draw(overlay)
    for lane in densitometry_input.lanes:
        draw.rectangle(_draw_bounds(lane.region), outline=(44, 110, 185), width=1)
    for target in densitometry_input.targets:
        color = (240, 168, 36) if target.is_loading_control else (43, 139, 87)
        draw.rectangle(_draw_bounds(target.region), outline=color, width=1)
    for band in densitometry_input.bands:
        draw.rectangle(_draw_bounds(band.region), outline=(204, 52, 62), width=2)
    buffer = io.BytesIO()
    overlay.save(buffer, format="PNG", optimize=False, compress_level=9)
    return buffer.getvalue()


def _pixel_bounds(region: BoundingRegion) -> tuple[int, int, int, int]:
    return (
        max(0, math.floor(region.x)),
        max(0, math.floor(region.y)),
        min(region.canvas_width, math.ceil(region.x + region.width)),
        min(region.canvas_height, math.ceil(region.y + region.height)),
    )


def _intersection_bounds(
    first: BoundingRegion,
    second: BoundingRegion,
) -> tuple[int, int, int, int]:
    x0 = max(first.x, second.x)
    y0 = max(first.y, second.y)
    x1 = min(first.x + first.width, second.x + second.width)
    y1 = min(first.y + first.height, second.y + second.height)
    if x1 <= x0 or y1 <= y0:
        raise InvalidDensitometryInput("lane and target regions do not intersect")
    return _pixel_bounds(
        BoundingRegion(
            region_id=first.region_id,
            source_artifact_id=first.source_artifact_id,
            x=float(x0),
            y=float(y0),
            width=float(x1 - x0),
            height=float(y1 - y0),
            canvas_width=first.canvas_width,
            canvas_height=first.canvas_height,
        )
    )


def _draw_bounds(region: BoundingRegion) -> tuple[int, int, int, int]:
    x0, y0, x1, y1 = _pixel_bounds(region)
    return x0, y0, max(x0, x1 - 1), max(y0, y1 - 1)


def _flag(
    input_sha256: str,
    code: DensitometryQcCode,
    severity: DensitometryQcSeverity,
    message: str,
    *,
    lane_id: UUID | None = None,
    target_id: UUID | None = None,
    band_id: UUID | None = None,
    metric_value: float | None = None,
) -> DensitometryQcFlag:
    scope = f"{lane_id or ''}:{target_id or ''}:{band_id or ''}"
    return DensitometryQcFlag(
        flag_id=_stable_id(input_sha256, f"qc:{code.value}:{scope}"),
        code=code,
        severity=severity,
        message=message,
        lane_id=lane_id,
        target_id=target_id,
        band_id=band_id,
        metric_value=metric_value,
    )


def _canonical_json(value: Any) -> str:
    return json.dumps(
        value.model_dump(mode="json"),
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
    )


def _with_normalized(
    value: DensitometryMeasurement,
    normalized_intensity: float | None,
) -> DensitometryMeasurement:
    return DensitometryMeasurement.model_validate(
        {
            **value.model_dump(mode="python"),
            "normalized_intensity": normalized_intensity,
        }
    )


def _rounded(value: float) -> float:
    return float(round(value, NUMERICAL_PRECISION_DECIMAL_PLACES))


def _stable_id(input_sha256: str, key: str) -> UUID:
    return uuid5(NAMESPACE_URL, f"urn:hiveblot:densitometry:{input_sha256}:{key}")
