"""Builders for versioned pipeline registry tests."""

from datetime import UTC, datetime
from uuid import UUID, uuid4

from hiveblot_contracts import (
    ArtifactReference,
    ModelIdentifier,
    ModelPipelineComponent,
    OutputSchemaIdentifier,
    PipelineDefinitionRecord,
    PipelineIdentifier,
    ToolIdentifier,
    ToolPipelineComponent,
)

NOW = datetime(2026, 8, 2, 20, tzinfo=UTC)


def artifact_reference(artifact_id: UUID | None = None) -> ArtifactReference:
    return ArtifactReference(
        artifact_id=artifact_id or uuid4(),
        sha256="a" * 64,
        media_type="image/png",
        byte_size=128,
    )


def pipeline_definition() -> PipelineDefinitionRecord:
    detect = ModelPipelineComponent(
        component_key="detect_regions",
        model=ModelIdentifier(
            provider="local",
            name="western-blot-detector",
            version="1.0",
        ),
        prompt_version="detect-v1",
        output_schema=OutputSchemaIdentifier(name="spatial-annotation-set", version="1.0"),
        configuration_json='{"temperature":0}',
    )
    normalize = ToolPipelineComponent(
        component_key="normalize_metadata",
        depends_on=(detect.component_key,),
        tool=ToolIdentifier(name="western-blot-normalizer", version="1.0"),
        output_schema=OutputSchemaIdentifier(
            name="western-blot-structured-annotation",
            version="1.0",
        ),
        configuration_json="{}",
    )
    return PipelineDefinitionRecord(
        definition_id=uuid4(),
        pipeline=PipelineIdentifier(name="western-blot-extraction", version="1.0"),
        description="Fixture extraction graph",
        components=(detect, normalize),
        configuration_json='{"fixture":true}',
        created_at=NOW,
    )
