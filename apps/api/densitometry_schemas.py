"""HTTP-only contracts for deterministic densitometry review and replay."""

from __future__ import annotations

import json
from datetime import datetime
from typing import Annotated, Literal, Self
from uuid import UUID

from hiveblot_contracts import (
    ArtifactReference,
    ContractModel,
    DensitometryBackgroundMethod,
    DensitometryBandRegion,
    DensitometryConfiguration,
    DensitometryGeometryReference,
    DensitometryImageKind,
    DensitometryLaneRegion,
    DensitometryNormalizationMethod,
    DensitometryResult,
    DensitometryTargetRegion,
    PredictionGeometryReference,
    ReviewerGeometryReference,
)
from pydantic import (
    BeforeValidator,
    Field,
    StrictBool,
    StrictFloat,
    StrictInt,
    TypeAdapter,
    model_validator,
)

from .evaluation_schemas import JsonUUID

_GEOMETRY_ADAPTER: TypeAdapter[DensitometryGeometryReference] = TypeAdapter(
    DensitometryGeometryReference
)


def _json_geometry(value: object) -> object:
    if isinstance(value, (PredictionGeometryReference, ReviewerGeometryReference)):
        return value
    return _GEOMETRY_ADAPTER.validate_json(json.dumps(value), strict=True)


type JsonDensitometryGeometryReference = Annotated[
    DensitometryGeometryReference,
    BeforeValidator(_json_geometry),
]


class DensitometryConfigurationRequest(ContractModel):
    background_method: Literal["none", "global_percentile", "local_border"] = "global_percentile"
    normalization_method: Literal["none", "loading_control"] = "loading_control"
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

    def to_contract(self) -> DensitometryConfiguration:
        return DensitometryConfiguration(
            background_method=DensitometryBackgroundMethod(self.background_method),
            normalization_method=DensitometryNormalizationMethod(self.normalization_method),
            background_percentile=self.background_percentile,
            local_border_pixels=self.local_border_pixels,
            saturation_black_level=self.saturation_black_level,
            saturation_fraction_threshold=self.saturation_fraction_threshold,
            minimum_band_width_pixels=self.minimum_band_width_pixels,
            minimum_band_height_pixels=self.minimum_band_height_pixels,
            minimum_band_area_pixels=self.minimum_band_area_pixels,
            uneven_background_cv_threshold=self.uneven_background_cv_threshold,
            lane_boundaries_reviewed=self.lane_boundaries_reviewed,
            exposure_known=self.exposure_known,
        )


class StartDensitometryRunRequest(ContractModel):
    image_artifact_id: JsonUUID
    geometry: JsonDensitometryGeometryReference
    loading_control_target_id: JsonUUID | None = None
    visibility: Literal["public", "organization_private"] | None = None
    organization_id: JsonUUID | None = None
    configuration: DensitometryConfigurationRequest = Field(
        default_factory=DensitometryConfigurationRequest
    )
    trace_id: JsonUUID | None = None

    @model_validator(mode="after")
    def explicit_scope_is_complete(self) -> Self:
        if self.visibility == "public" and self.organization_id is not None:
            raise ValueError("public densitometry runs cannot include organization_id")
        if self.visibility == "organization_private" and self.organization_id is None:
            raise ValueError("organization-private densitometry runs require organization_id")
        if self.visibility is None and self.organization_id is not None:
            raise ValueError("organization_id requires an explicit visibility")
        return self


class ReplayDensitometryRequest(ContractModel):
    trace_id: JsonUUID | None = None


class DensitometryGeometryOptionResponse(ContractModel):
    geometry: DensitometryGeometryReference
    label: str
    created_at: datetime
    image_artifact: ArtifactReference
    image_kind: DensitometryImageKind
    image_download_url: str
    image_download_expires_at: datetime
    lanes: tuple[DensitometryLaneRegion, ...]
    targets: tuple[DensitometryTargetRegion, ...]
    bands: tuple[DensitometryBandRegion, ...]
    inferred_loading_control_target_ids: tuple[UUID, ...]


class DensitometryAttemptResponse(ContractModel):
    run_id: UUID
    definition_id: UUID
    case_id: UUID
    invocation_id: UUID
    replay_of_invocation_id: UUID | None
    publication_id: UUID
    trace_id: UUID
    visibility: Literal["public", "organization_private"]
    organization_id: UUID | None
    created_at: datetime
    result: DensitometryResult
    source_image_download_url: str
    source_image_download_expires_at: datetime
    overlay_download_url: str
    overlay_download_expires_at: datetime


class DensitometryWorkbenchResponse(ContractModel):
    case_id: UUID
    geometry_options: tuple[DensitometryGeometryOptionResponse, ...]
    attempts: tuple[DensitometryAttemptResponse, ...]
