"""Persistence protocol for pipeline definitions, runs, invocations, and publications."""

from __future__ import annotations

from collections.abc import Sequence
from typing import Protocol
from uuid import UUID

from hiveblot_contracts import (
    ComponentInvocationRecord,
    PipelineDefinitionRecord,
    PipelineIdentifier,
    PipelinePublicationRecord,
    PipelineRunRecord,
)


class PipelineRunRepository(Protocol):
    def create_definition(
        self,
        definition: PipelineDefinitionRecord,
    ) -> PipelineDefinitionRecord: ...

    def get_definition(self, definition_id: UUID) -> PipelineDefinitionRecord | None: ...

    def get_definition_by_pipeline(
        self,
        pipeline: PipelineIdentifier,
    ) -> PipelineDefinitionRecord | None: ...

    def list_definitions(self) -> Sequence[PipelineDefinitionRecord]: ...

    def create_run(
        self,
        run: PipelineRunRecord,
        invocations: tuple[ComponentInvocationRecord, ...],
    ) -> PipelineRunRecord: ...

    def get_run(self, run_id: UUID) -> PipelineRunRecord | None: ...

    def list_runs(self, case_id: UUID) -> Sequence[PipelineRunRecord]: ...

    def get_invocation(self, invocation_id: UUID) -> ComponentInvocationRecord | None: ...

    def list_invocations(self, run_id: UUID) -> Sequence[ComponentInvocationRecord]: ...

    def complete_invocation(
        self,
        invocation: ComponentInvocationRecord,
    ) -> ComponentInvocationRecord: ...

    def create_replay(
        self,
        invocation: ComponentInvocationRecord,
    ) -> ComponentInvocationRecord: ...

    def publish(
        self,
        publication: PipelinePublicationRecord,
        updated_run: PipelineRunRecord,
    ) -> PipelinePublicationRecord: ...

    def list_publications(self, run_id: UUID) -> Sequence[PipelinePublicationRecord]: ...
