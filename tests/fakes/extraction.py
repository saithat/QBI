from __future__ import annotations

import hashlib
import json
from datetime import UTC, datetime
from pathlib import Path
from uuid import NAMESPACE_URL, UUID, uuid4, uuid5

from hiveblot_contracts import (
    ArtifactAcquisitionMethod,
    ArtifactRecord,
    ArtifactVisibility,
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
from hiveblot_extraction import (
    DetectionExecution,
    ModelExecution,
    RawCandidateModelResponse,
)

FIXTURE_PATH = (
    Path(__file__).parents[1] / "fixtures" / "baseline" / "oduah_2024_page_4_model_output.json"
)


class InMemoryExtractionArtifactReader:
    def __init__(self, artifact: ArtifactRecord, content: bytes) -> None:
        self.artifact = artifact
        self.content = content

    def get_artifact(self, artifact_id: UUID) -> ArtifactRecord:
        if artifact_id != self.artifact.artifact_id:
            raise KeyError(artifact_id)
        return self.artifact

    def read_bytes(self, artifact_id: UUID) -> bytes:
        self.get_artifact(artifact_id)
        return self.content


class FixtureExtractionImplementation:
    def __init__(
        self,
        *,
        invalid_output: bool = False,
        report_cost: bool = True,
        version: str = "1.0.0",
    ) -> None:
        self.invalid_output = invalid_output
        self.report_cost = report_cost
        self._identity = WesternBlotExtractionImplementation(
            implementation_name="fixture-western-blot",
            implementation_version=version,
            pipeline=PipelineIdentifier(
                name="western-blot-extraction-fixture",
                version=version,
            ),
            detector=ToolIdentifier(name="fixture-detector", version="1.0.0"),
            model=ModelIdentifier(
                provider="fixture",
                name="captured-qwen3-vl",
                version="oduah-page-4",
            ),
            prompt_version="fixture-prompt-v1",
            assembler=ToolIdentifier(name="fixture-assembler", version="1.0.0"),
        )
        fixture = json.loads(FIXTURE_PATH.read_text(encoding="utf-8"))
        self.raw_output = json.dumps(fixture["extraction"], sort_keys=True)

    @property
    def identity(self) -> WesternBlotExtractionImplementation:
        return self._identity

    def detect(
        self,
        extraction_input: WesternBlotExtractionInput,
        content: bytes,
    ) -> DetectionExecution:
        del content
        candidate_id = fixture_candidate_id(extraction_input.source_artifact.artifact_id)
        page_number = 4 if extraction_input.source_kind is WesternBlotSourceKind.PDF else None
        region = BoundingRegion(
            region_id=uuid5(candidate_id, "candidate-region"),
            source_artifact_id=extraction_input.source_artifact.artifact_id,
            x=0.0,
            y=288.0,
            width=2696.0,
            height=2658.0,
            canvas_width=2696,
            canvas_height=3500,
            page_number=page_number,
        )
        result = WesternBlotFigureCandidateSet(
            source_artifact=extraction_input.source_artifact,
            source_kind=extraction_input.source_kind,
            detector=self.identity.detector,
            candidates=(
                WesternBlotFigureCandidate(
                    candidate_id=candidate_id,
                    source_artifact=extraction_input.source_artifact,
                    region=region,
                    tight_region=region,
                    detector_score=0.7784390975201184,
                ),
            ),
        )
        return DetectionExecution(
            raw_output_json=json.dumps(
                {"candidate_id": str(candidate_id), "fixture": True},
                sort_keys=True,
            ),
            result=result,
            latency_ms=3,
        )

    def infer(
        self,
        extraction_input: WesternBlotExtractionInput,
        content: bytes,
        candidates: WesternBlotFigureCandidateSet,
    ) -> ModelExecution:
        del extraction_input, content
        raw_output = '{"unexpected":true}' if self.invalid_output else self.raw_output
        return ModelExecution(
            responses=tuple(
                RawCandidateModelResponse(
                    candidate_id=candidate.candidate_id,
                    raw_output=raw_output,
                    latency_ms=7,
                    cost_microusd=11 if self.report_cost else None,
                )
                for candidate in candidates.candidates
            )
        )


def extraction_artifact(
    *,
    media_type: str = "application/pdf",
    content: bytes = b"fixture stored scientific source",
) -> ArtifactRecord:
    return ArtifactRecord(
        artifact_id=uuid4(),
        sha256=hashlib.sha256(content).hexdigest(),
        media_type=media_type,
        byte_size=len(content),
        original_filename="fixture.pdf" if media_type == "application/pdf" else "fixture.png",
        source_uri="urn:hiveblot:test:extraction-fixture",
        acquisition_method=ArtifactAcquisitionMethod.USER_UPLOAD,
        visibility=ArtifactVisibility.PUBLIC,
        organization_id=None,
        relationships=(),
        created_at=datetime(2026, 8, 2, 22, tzinfo=UTC),
    )


def fixture_candidate_id(artifact_id: UUID) -> UUID:
    return uuid5(NAMESPACE_URL, f"urn:hiveblot:test:figure-candidate:{artifact_id}")
