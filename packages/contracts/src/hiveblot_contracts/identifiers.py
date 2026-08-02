"""Versioned identifiers for pipeline, model, and deterministic tool producers."""

from __future__ import annotations

from typing import Annotated, Literal

from pydantic import Field

from .base import ContractModel, Identifier


class PipelineIdentifier(ContractModel):
    name: Identifier
    version: Identifier


class ModelIdentifier(ContractModel):
    kind: Literal["model"] = "model"
    provider: Identifier
    name: Identifier
    version: Identifier


class ToolIdentifier(ContractModel):
    kind: Literal["tool"] = "tool"
    name: Identifier
    version: Identifier


type ProducerIdentifier = Annotated[
    ModelIdentifier | ToolIdentifier,
    Field(discriminator="kind"),
]
