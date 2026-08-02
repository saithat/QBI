"""Strict contracts for deterministic western-blot densitometry."""

from __future__ import annotations

import math
from collections.abc import Hashable, Iterable
from enum import StrEnum
from typing import Annotated, Literal, Self
from uuid import UUID

from pydantic import Field, StrictBool, StrictFloat, StrictInt, model_validator

from .artifacts import ArtifactReference, BoundingRegion
from .base import ContractModel, Identifier, Sha256Digest
from .identifiers import PipelineIdentifier, ToolIdentifier


class DensitometryImageKind(StrEnum):
    RAW_SOURCE = "raw_source"
    PUBLICATION_FIGURE = "publication_figure"


class DensitometryBackgroundMethod(StrEnum):
    NONE = "none"
    GLOBAL_PERCENTILE = "global_percentile"
    LOCAL_BORDER = "local_border"


class DensitometryNormalizationMethod(StrEnum):
    NONE = "none"
    LOADING_CONTROL = "loading_control"


class DensitometrySuitability(StrEnum):
    QUANTITATIVE = "quantitative"
    SEMI_QUANTITATIVE = "semi_quantitative"
    EXPLORATORY_ONLY = "exploratory_only"
    NOT_ANALYZABLE = "not_analyzable"


class DensitometryQcSeverity(StrEnum):
    WARNING = "warning"
    ERROR = "error"


class DensitometryQcCode(StrEnum):
    SATURATION = "saturation"
    INSUFFICIENT_RESOLUTION = "insufficient_resolution"
    MISSING_LOADING_CONTROL = "missing_loading_control"
    ZERO_LOADING_CONTROL = "zero_loading_control"
    UNCLEAR_LANE_BOUNDARIES = "unclear_lane_boundaries"
    PUBLICATION_FIGURE_SOURCE = "publication_figure_source"
    UNKNOWN_EXPOSURE = "unknown_exposure"
    UNEVEN_BACKGROUND = "uneven_background"
    EMPTY_BACKGROUND = "empty_background"


class PredictionGeometryReference(ContractModel):
    source_type: Literal["prediction"] = "prediction"
    prediction_id: UUID


class ReviewerGeometryReference(ContractModel):
    source_type: Literal["reviewer_revision"] = "reviewer_revision"
    annotation_revision_id: UUID


type DensitometryGeometryReference = Annotated[
    PredictionGeometryReference | ReviewerGeometryReference,
    Field(discriminator="source_type"),
]


class DensitometryLaneRegion(ContractModel):
    lane_id: UUID
    lane_index: StrictInt = Field(ge=1)
    label: str | None = Field(default=None, min_length=1, max_length=1000)
    region: BoundingRegion


class DensitometryTargetRegion(ContractModel):
    target_id: UUID
    label: str = Field(min_length=1, max_length=1000)
    is_loading_control: StrictBool
    region: BoundingRegion


class DensitometryBandRegion(ContractModel):
    band_id: UUID
    lane_id: UUID
    target_id: UUID
    region: BoundingRegion


class DensitometryConfiguration(ContractModel):
    background_method: DensitometryBackgroundMethod = DensitometryBackgroundMethod.GLOBAL_PERCENTILE
    normalization_method: DensitometryNormalizationMethod = (
        DensitometryNormalizationMethod.LOADING_CONTROL
    )
    background_percentile: StrictFloat = Field(default=20.0, ge=0, le=100)
    local_border_pixels: StrictInt = Field(default=4, ge=1, le=100)
    saturation_black_level: StrictInt = Field(default=4, ge=0, le=254)
    saturation_fraction_threshold: StrictFloat = Field(default=0.05, gt=0, le=1)
    minimum_band_width_pixels: StrictInt = Field(default=3, ge=1, le=10_000)
    minimum_band_height_pixels: StrictInt = Field(default=2, ge=1, le=10_000)
    minimum_band_area_pixels: StrictInt = Field(default=9, ge=1, le=100_000_000)
    uneven_background_cv_threshold: StrictFloat = Field(default=0.35, gt=0, le=10)
    lane_boundaries_reviewed: StrictBool = False
    exposure_known: StrictBool = False


class DensitometryInput(ContractModel):
    case_id: UUID
    image_artifact: ArtifactReference
    image_kind: DensitometryImageKind
    geometry: DensitometryGeometryReference
    lanes: tuple[DensitometryLaneRegion, ...] = Field(min_length=1)
    targets: tuple[DensitometryTargetRegion, ...] = Field(min_length=1)
    bands: tuple[DensitometryBandRegion, ...] = Field(min_length=1)
    loading_control_target_id: UUID | None = None
    configuration: DensitometryConfiguration

    @model_validator(mode="after")
    def geometry_is_complete_and_uses_the_image(self) -> Self:
        if not self.image_artifact.media_type.startswith("image/"):
            raise ValueError("densitometry requires an immutable image artifact")
        _require_unique((item.lane_id for item in self.lanes), "lane IDs")
        _require_unique((item.lane_index for item in self.lanes), "lane indices")
        _require_unique((item.target_id for item in self.targets), "target IDs")
        _require_unique((item.band_id for item in self.bands), "band IDs")
        _require_unique(
            ((item.lane_id, item.target_id) for item in self.bands),
            "lane/target band pairs",
        )
        lanes = {item.lane_id: item for item in self.lanes}
        targets = {item.target_id: item for item in self.targets}
        regions = (
            *(item.region for item in self.lanes),
            *(item.region for item in self.targets),
            *(item.region for item in self.bands),
        )
        for region in regions:
            if region.source_artifact_id != self.image_artifact.artifact_id:
                raise ValueError("densitometry geometry must reference the exact image artifact")
            if region.page_number is not None:
                raise ValueError("densitometry geometry must use an image, not PDF page pixels")
            if (
                region.canvas_width != self.lanes[0].region.canvas_width
                or region.canvas_height != self.lanes[0].region.canvas_height
            ):
                raise ValueError("densitometry geometry must use one source coordinate space")
        for band in self.bands:
            lane = lanes.get(band.lane_id)
            target = targets.get(band.target_id)
            if lane is None or target is None:
                raise ValueError("band regions must reference input lanes and targets")
            if not _contains(lane.region, band.region) or not _contains(target.region, band.region):
                raise ValueError("band regions must fit inside their lane and target regions")
        expected_pairs = {(lane_id, target_id) for lane_id in lanes for target_id in targets}
        actual_pairs = {(item.lane_id, item.target_id) for item in self.bands}
        if actual_pairs != expected_pairs:
            raise ValueError("densitometry requires one band region for every lane/target pair")
        if self.loading_control_target_id is not None:
            control = targets.get(self.loading_control_target_id)
            if control is None or not control.is_loading_control:
                raise ValueError("loading control must reference a loading-control target region")
        return self


class DensitometryQcFlag(ContractModel):
    flag_id: UUID
    code: DensitometryQcCode
    severity: DensitometryQcSeverity
    message: str = Field(min_length=1, max_length=2000)
    lane_id: UUID | None = None
    target_id: UUID | None = None
    band_id: UUID | None = None
    metric_value: StrictFloat | None = None


class DensitometryMeasurement(ContractModel):
    measurement_id: UUID
    lane_id: UUID
    lane_index: StrictInt = Field(ge=1)
    lane_label: str | None = Field(default=None, min_length=1, max_length=1000)
    target_id: UUID
    target_label: str = Field(min_length=1, max_length=1000)
    band_id: UUID
    pixel_count: StrictInt = Field(gt=0)
    raw_intensity: StrictFloat = Field(ge=0)
    background_per_pixel: StrictFloat = Field(ge=0, le=255)
    background_estimate: StrictFloat = Field(ge=0)
    corrected_intensity: StrictFloat = Field(ge=0)
    normalized_intensity: StrictFloat | None = Field(default=None, ge=0)
    qc_flags: tuple[DensitometryQcFlag, ...] = ()

    @model_validator(mode="after")
    def measurement_qc_is_scoped(self) -> Self:
        _require_unique((item.flag_id for item in self.qc_flags), "measurement QC flag IDs")
        for flag in self.qc_flags:
            if flag.lane_id not in {None, self.lane_id}:
                raise ValueError("measurement QC lane must match its measurement")
            if flag.target_id not in {None, self.target_id}:
                raise ValueError("measurement QC target must match its measurement")
            if flag.band_id not in {None, self.band_id}:
                raise ValueError("measurement QC band must match its measurement")
        maximum_intensity = 255.0 * self.pixel_count
        if self.raw_intensity > maximum_intensity:
            raise ValueError("raw intensity exceeds the 8-bit pixel maximum")
        if self.background_estimate > maximum_intensity:
            raise ValueError("background estimate exceeds the 8-bit pixel maximum")
        if self.corrected_intensity > self.raw_intensity:
            raise ValueError("corrected intensity cannot exceed raw intensity")
        return self


class DensitometryReproducibility(ContractModel):
    algorithm: Identifier
    tool: ToolIdentifier
    input_sha256: Sha256Digest
    configuration_sha256: Sha256Digest
    source_image_sha256: Sha256Digest
    numerical_precision_decimal_places: StrictInt = Field(ge=0, le=15)
    library_versions: tuple[ToolIdentifier, ...]

    @model_validator(mode="after")
    def library_names_are_unique(self) -> Self:
        _require_unique((item.name for item in self.library_versions), "library names")
        return self


class DensitometryResult(ContractModel):
    result_id: UUID
    input: DensitometryInput
    tool: ToolIdentifier
    suitability: DensitometrySuitability
    measurements: tuple[DensitometryMeasurement, ...]
    global_qc_flags: tuple[DensitometryQcFlag, ...]
    analysis_overlay: ArtifactReference
    reproducibility: DensitometryReproducibility

    @model_validator(mode="after")
    def result_is_complete_and_consistent(self) -> Self:
        if self.tool != self.reproducibility.tool:
            raise ValueError("result and reproducibility tool identities must match")
        if self.reproducibility.source_image_sha256 != self.input.image_artifact.sha256:
            raise ValueError("reproducibility metadata must name the exact source image hash")
        if self.analysis_overlay.media_type != "image/png":
            raise ValueError("densitometry analysis overlay must be a PNG artifact")
        _require_unique((item.measurement_id for item in self.measurements), "measurement IDs")
        _require_unique((item.flag_id for item in self.global_qc_flags), "global QC flag IDs")
        lanes = {item.lane_id: item for item in self.input.lanes}
        targets = {item.target_id: item for item in self.input.targets}
        bands = {item.band_id: item for item in self.input.bands}
        expected = {(item.lane_id, item.target_id, item.band_id) for item in self.input.bands}
        actual = {(item.lane_id, item.target_id, item.band_id) for item in self.measurements}
        if actual != expected:
            raise ValueError("densitometry results must cover every input band region")
        for measurement in self.measurements:
            lane = lanes[measurement.lane_id]
            target = targets[measurement.target_id]
            band = bands[measurement.band_id]
            if band.lane_id != lane.lane_id or band.target_id != target.target_id:
                raise ValueError("measurement lane and target must match its input band")
            if (
                measurement.lane_index != lane.lane_index
                or measurement.lane_label != lane.label
                or measurement.target_label != target.label
            ):
                raise ValueError("measurement labels and lane index must match the input geometry")
        all_flags = (
            *self.global_qc_flags,
            *(flag for measurement in self.measurements for flag in measurement.qc_flags),
        )
        _require_unique((item.flag_id for item in all_flags), "result QC flag IDs")
        for flag in all_flags:
            if flag.lane_id is not None and flag.lane_id not in lanes:
                raise ValueError("QC lane must belong to the input geometry")
            if flag.target_id is not None and flag.target_id not in targets:
                raise ValueError("QC target must belong to the input geometry")
            if flag.band_id is not None and flag.band_id not in bands:
                raise ValueError("QC band must belong to the input geometry")
        self._validate_normalized_values()
        if (
            any(flag.severity is DensitometryQcSeverity.ERROR for flag in all_flags)
            and self.suitability is not DensitometrySuitability.NOT_ANALYZABLE
        ):
            raise ValueError("error-level QC requires a not-analyzable suitability")
        if (
            self.input.image_kind is DensitometryImageKind.PUBLICATION_FIGURE
            and self.suitability
            not in {
                DensitometrySuitability.EXPLORATORY_ONLY,
                DensitometrySuitability.NOT_ANALYZABLE,
            }
        ):
            raise ValueError("publication-figure measurements cannot claim quantitative status")
        return self

    def _validate_normalized_values(self) -> None:
        method = self.input.configuration.normalization_method
        tolerance = 2 * 10 ** (-self.reproducibility.numerical_precision_decimal_places)
        if method is DensitometryNormalizationMethod.NONE:
            if any(
                item.normalized_intensity is None
                or not math.isclose(
                    item.normalized_intensity,
                    item.corrected_intensity,
                    rel_tol=0,
                    abs_tol=tolerance,
                )
                for item in self.measurements
            ):
                raise ValueError("unnormalized results must expose corrected intensity")
            return
        control_id = self.input.loading_control_target_id
        if control_id is None:
            if any(item.normalized_intensity is not None for item in self.measurements):
                raise ValueError("missing loading controls cannot produce normalized intensity")
            return
        controls = {
            item.lane_id: item.corrected_intensity
            for item in self.measurements
            if item.target_id == control_id
        }
        for item in self.measurements:
            control = controls[item.lane_id]
            if control == 0:
                if item.normalized_intensity is not None:
                    raise ValueError("zero loading controls cannot produce normalized intensity")
                continue
            expected_value = round(
                item.corrected_intensity / control,
                self.reproducibility.numerical_precision_decimal_places,
            )
            if item.normalized_intensity is None or not math.isclose(
                item.normalized_intensity,
                expected_value,
                rel_tol=0,
                abs_tol=tolerance,
            ):
                raise ValueError("normalized intensity does not match its lane loading control")


class DensitometryRunRecord(ContractModel):
    run_id: UUID
    definition_id: UUID
    case_id: UUID
    invocation_id: UUID
    publication_id: UUID
    trace_id: UUID
    pipeline: PipelineIdentifier
    result: DensitometryResult

    @model_validator(mode="after")
    def run_identities_match_result(self) -> Self:
        if self.case_id != self.result.input.case_id:
            raise ValueError("densitometry run and input case IDs must match")
        return self


class DensitometryReplayRecord(ContractModel):
    run_id: UUID
    case_id: UUID
    invocation_id: UUID
    replay_of_invocation_id: UUID
    publication_id: UUID
    trace_id: UUID
    result: DensitometryResult

    @model_validator(mode="after")
    def replay_identities_match_result(self) -> Self:
        if self.case_id != self.result.input.case_id:
            raise ValueError("densitometry replay and input case IDs must match")
        if self.invocation_id == self.replay_of_invocation_id:
            raise ValueError("densitometry replay must receive a new invocation ID")
        return self


def _contains(parent: BoundingRegion, child: BoundingRegion) -> bool:
    return (
        child.x >= parent.x
        and child.y >= parent.y
        and child.x + child.width <= parent.x + parent.width
        and child.y + child.height <= parent.y + parent.height
    )


def _require_unique(values: Iterable[Hashable], label: str) -> None:
    materialized = tuple(values)
    if len(materialized) != len(set(materialized)):
        raise ValueError(f"{label} must be unique")
