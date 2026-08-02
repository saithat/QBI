import hashlib
import json
from pathlib import Path
from uuid import uuid4

from hiveblot_contracts import (
    ArtifactReference,
    DensitometryInput,
    DensitometryQcCode,
    DensitometrySuitability,
)
from hiveblot_densitometry import DeterministicDensitometryTool

from tests.fakes.densitometry import (
    synthetic_blot_png,
    synthetic_densitometry_input,
)

REFERENCE = Path("tests/fixtures/densitometry/synthetic_reference.json")


def test_synthetic_reference_measurements_are_exact_and_reproducible() -> None:
    densitometry_input = synthetic_densitometry_input()
    content = synthetic_blot_png()
    tool = DeterministicDensitometryTool()

    first = tool.analyze(densitometry_input, content)
    second = tool.analyze(densitometry_input, content)

    assert first == second
    assert first.overlay_png == second.overlay_png
    assert first.suitability is DensitometrySuitability.QUANTITATIVE
    assert first.global_qc_flags == ()
    expected = json.loads(REFERENCE.read_text(encoding="utf-8"))["measurements"]
    actual = [
        {
            "lane_index": item.lane_index,
            "target": item.target_label,
            "pixel_count": item.pixel_count,
            "raw_intensity": item.raw_intensity,
            "background_estimate": item.background_estimate,
            "corrected_intensity": item.corrected_intensity,
            "normalized_intensity": item.normalized_intensity,
        }
        for item in first.measurements
    ]
    assert actual == expected

    overlay = ArtifactReference(
        artifact_id=uuid4(),
        sha256=hashlib.sha256(first.overlay_png).hexdigest(),
        media_type="image/png",
        byte_size=len(first.overlay_png),
    )
    assert first.result(overlay) == second.result(overlay)


def test_one_pixel_geometry_perturbation_changes_only_the_expected_measurement() -> None:
    tool = DeterministicDensitometryTool()
    baseline = tool.analyze(synthetic_densitometry_input(), synthetic_blot_png())
    shifted = tool.analyze(
        synthetic_densitometry_input(first_band_x=6.0),
        synthetic_blot_png(),
    )

    assert shifted.input_sha256 != baseline.input_sha256
    assert shifted.measurements[0].corrected_intensity == 5040.0
    assert shifted.measurements[0].normalized_intensity == 1.05
    assert [item.model_dump(exclude={"measurement_id"}) for item in shifted.measurements[1:]] == [
        item.model_dump(exclude={"measurement_id"}) for item in baseline.measurements[1:]
    ]


def test_publication_and_missing_control_are_explicit_quality_states() -> None:
    tool = DeterministicDensitometryTool()
    publication = tool.analyze(
        synthetic_densitometry_input(publication=True),
        synthetic_blot_png(),
    )
    missing_control = tool.analyze(
        synthetic_densitometry_input(loading_control_target_id=None),
        synthetic_blot_png(),
    )

    assert publication.suitability is DensitometrySuitability.EXPLORATORY_ONLY
    assert DensitometryQcCode.PUBLICATION_FIGURE_SOURCE in {
        item.code for item in publication.global_qc_flags
    }
    assert missing_control.suitability is DensitometrySuitability.NOT_ANALYZABLE
    assert DensitometryQcCode.MISSING_LOADING_CONTROL in {
        item.code for item in missing_control.global_qc_flags
    }
    assert all(item.normalized_intensity is None for item in missing_control.measurements)


def test_global_background_statistics_exclude_the_measured_band() -> None:
    original = synthetic_densitometry_input()
    densitometry_input = DensitometryInput.model_validate(
        {
            **original.model_dump(mode="python"),
            "configuration": {
                **original.configuration.model_dump(mode="python"),
                "uneven_background_cv_threshold": 0.35,
            },
        }
    )

    computation = DeterministicDensitometryTool().analyze(
        densitometry_input,
        synthetic_blot_png(),
    )

    assert DensitometryQcCode.UNEVEN_BACKGROUND not in {
        flag.code for measurement in computation.measurements for flag in measurement.qc_flags
    }
