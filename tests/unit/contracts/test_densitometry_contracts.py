from __future__ import annotations

import hashlib
from uuid import uuid4

import pytest
from hiveblot_contracts import (
    ArtifactReference,
    DensitometryInput,
    DensitometryResult,
    DensitometrySuitability,
)
from hiveblot_densitometry import DeterministicDensitometryTool
from pydantic import ValidationError

from tests.fakes.densitometry import synthetic_blot_png, synthetic_densitometry_input


def test_densitometry_input_rejects_incomplete_or_out_of_bounds_geometry() -> None:
    missing_band = synthetic_densitometry_input().model_dump(mode="python")
    missing_band["bands"] = missing_band["bands"][:-1]
    with pytest.raises(ValidationError, match="one band region for every"):
        DensitometryInput.model_validate(missing_band)

    outside_lane = synthetic_densitometry_input().model_dump(mode="python")
    outside_lane["bands"][0]["region"]["x"] = 16.0
    with pytest.raises(ValidationError, match="fit inside"):
        DensitometryInput.model_validate(outside_lane)


def test_result_cannot_overstate_publication_figure_suitability() -> None:
    source = synthetic_densitometry_input(publication=True)
    computation = DeterministicDensitometryTool().analyze(source, synthetic_blot_png())
    result_payload = computation.result(
        ArtifactReference(
            artifact_id=uuid4(),
            sha256=hashlib.sha256(computation.overlay_png).hexdigest(),
            media_type="image/png",
            byte_size=len(computation.overlay_png),
        )
    ).model_dump(mode="python")
    result_payload["suitability"] = DensitometrySuitability.QUANTITATIVE

    with pytest.raises(ValidationError, match="publication-figure"):
        DensitometryResult.model_validate(result_payload)


def test_result_rejects_measurements_inconsistent_with_input_and_control() -> None:
    source = synthetic_densitometry_input()
    computation = DeterministicDensitometryTool().analyze(source, synthetic_blot_png())
    result = computation.result(
        ArtifactReference(
            artifact_id=uuid4(),
            sha256=hashlib.sha256(computation.overlay_png).hexdigest(),
            media_type="image/png",
            byte_size=len(computation.overlay_png),
        )
    )
    wrong_label = result.model_dump(mode="python")
    wrong_label["measurements"][0]["target_label"] = "invented target"
    with pytest.raises(ValidationError, match="must match the input geometry"):
        DensitometryResult.model_validate(wrong_label)

    wrong_normalization = result.model_dump(mode="python")
    wrong_normalization["measurements"][0]["normalized_intensity"] = 99.0
    with pytest.raises(ValidationError, match="does not match its lane loading control"):
        DensitometryResult.model_validate(wrong_normalization)
