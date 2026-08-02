from uuid import uuid4

import pytest
from hiveblot_contracts import BoundingRegion, PredictionEvidence
from pydantic import ValidationError


def test_prediction_evidence_requires_location_or_description() -> None:
    with pytest.raises(ValidationError, match="must include"):
        PredictionEvidence(artifact_id=uuid4())


def test_prediction_evidence_region_must_match_its_artifact() -> None:
    with pytest.raises(ValidationError, match="evidence artifact"):
        PredictionEvidence(
            artifact_id=uuid4(),
            region=BoundingRegion(
                region_id=uuid4(),
                source_artifact_id=uuid4(),
                x=0.0,
                y=0.0,
                width=1.0,
                height=1.0,
                canvas_width=2,
                canvas_height=2,
            ),
        )
