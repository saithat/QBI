import io
import json
from pathlib import Path
from typing import Any
from uuid import uuid4

from hiveblot_contracts import (
    ArtifactReference,
    WesternBlotExtractionConfiguration,
    WesternBlotExtractionInput,
    WesternBlotSourceKind,
)
from hiveblot_extraction import LegacyVlmExtractionImplementation
from PIL import Image

from hiveblot.settings import Settings
from tests.fakes.extraction import FIXTURE_PATH


class CapturingVlmClient:
    def __init__(self, raw_output: str) -> None:
        self.raw_output = raw_output
        self.calls: list[dict[str, Any]] = []

    def extract_candidate_with_raw(
        self,
        candidate_path: str | Path,
        text_context: str,
        max_tokens: int = 4096,
        image_max_side: int | None = None,
    ) -> tuple[str, object]:
        with Image.open(candidate_path) as candidate:
            size = candidate.size
        self.calls.append(
            {
                "size": size,
                "text_context": text_context,
                "max_tokens": max_tokens,
                "image_max_side": image_max_side,
            }
        )
        return self.raw_output, json.loads(self.raw_output)


def test_legacy_adapter_processes_stored_image_bytes_and_preserves_raw_output() -> None:
    fixture = json.loads(FIXTURE_PATH.read_text(encoding="utf-8"))
    raw_output = json.dumps(fixture["extraction"], sort_keys=True)
    client = CapturingVlmClient(raw_output)
    implementation = LegacyVlmExtractionImplementation(
        Settings(vllm_model_revision="test-revision", _env_file=None),
        client=client,  # type: ignore[arg-type]
    )
    buffer = io.BytesIO()
    Image.new("RGB", (320, 160), "white").save(buffer, format="PNG")
    content = buffer.getvalue()
    artifact_id = uuid4()
    extraction_input = WesternBlotExtractionInput(
        source_artifact=ArtifactReference(
            artifact_id=artifact_id,
            sha256="1" * 64,
            media_type="image/png",
            byte_size=len(content),
        ),
        source_kind=WesternBlotSourceKind.IMAGE,
        configuration=WesternBlotExtractionConfiguration(
            implementation_name=implementation.identity.implementation_name,
            image_max_side=128,
            model_max_tokens=2048,
        ),
        trace_id=uuid4(),
    )

    detection = implementation.detect(extraction_input, content)
    execution = implementation.infer(extraction_input, content, detection.result)

    assert detection.result.candidates[0].region.canvas_width == 320
    assert detection.result.candidates[0].region.canvas_height == 160
    assert execution.responses[0].raw_output == raw_output
    assert execution.responses[0].cost_microusd is None
    assert client.calls == [
        {
            "size": (320, 160),
            "text_context": "",
            "max_tokens": 2048,
            "image_max_side": 128,
        }
    ]
    assert implementation.identity.model.version == "test-revision"
    assert implementation.identity.prompt_version.startswith("sha256:")
