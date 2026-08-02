"""In-memory pipeline registry persistence for service and API tests."""

from __future__ import annotations

from uuid import UUID

from hiveblot_contracts import (
    ComponentInvocationRecord,
    ComponentInvocationStatus,
    PipelineDefinitionRecord,
    PipelineIdentifier,
    PipelinePublicationRecord,
    PipelineRunRecord,
)
from hiveblot_evaluation import (
    ConcurrencyConflict,
    DuplicateEvaluationRecord,
    EvaluationNotFound,
)


class InMemoryPipelineRunRepository:
    def __init__(self) -> None:
        self.definitions: dict[UUID, PipelineDefinitionRecord] = {}
        self.runs: dict[UUID, PipelineRunRecord] = {}
        self.invocations: dict[UUID, ComponentInvocationRecord] = {}
        self.publications: dict[UUID, PipelinePublicationRecord] = {}

    def create_definition(
        self,
        definition: PipelineDefinitionRecord,
    ) -> PipelineDefinitionRecord:
        if any(item.pipeline == definition.pipeline for item in self.definitions.values()):
            raise DuplicateEvaluationRecord("pipeline definition already exists")
        self.definitions[definition.definition_id] = definition
        return definition

    def get_definition(self, definition_id: UUID) -> PipelineDefinitionRecord | None:
        return self.definitions.get(definition_id)

    def get_definition_by_pipeline(
        self,
        pipeline: PipelineIdentifier,
    ) -> PipelineDefinitionRecord | None:
        return next(
            (item for item in self.definitions.values() if item.pipeline == pipeline),
            None,
        )

    def list_definitions(self):
        return tuple(
            sorted(
                self.definitions.values(),
                key=lambda item: (item.pipeline.name, item.pipeline.version),
            )
        )

    def create_run(
        self,
        run: PipelineRunRecord,
        invocations: tuple[ComponentInvocationRecord, ...],
    ) -> PipelineRunRecord:
        if run.run_id in self.runs:
            raise DuplicateEvaluationRecord(str(run.run_id))
        self.runs[run.run_id] = run
        self.invocations.update({item.invocation_id: item for item in invocations})
        return run

    def get_run(self, run_id: UUID) -> PipelineRunRecord | None:
        return self.runs.get(run_id)

    def list_runs(self, case_id: UUID):
        return tuple(item for item in self.runs.values() if item.case_id == case_id)

    def get_invocation(self, invocation_id: UUID) -> ComponentInvocationRecord | None:
        return self.invocations.get(invocation_id)

    def list_invocations(self, run_id: UUID):
        return tuple(item for item in self.invocations.values() if item.run_id == run_id)

    def complete_invocation(
        self,
        invocation: ComponentInvocationRecord,
    ) -> ComponentInvocationRecord:
        current = self.invocations.get(invocation.invocation_id)
        if current is None:
            raise EvaluationNotFound(str(invocation.invocation_id))
        if current.status is not ComponentInvocationStatus.PENDING:
            raise ConcurrencyConflict("component invocation is already complete")
        self.invocations[invocation.invocation_id] = invocation
        return invocation

    def create_replay(
        self,
        invocation: ComponentInvocationRecord,
    ) -> ComponentInvocationRecord:
        if invocation.invocation_id in self.invocations:
            raise DuplicateEvaluationRecord(str(invocation.invocation_id))
        if invocation.replay_of_invocation_id not in self.invocations:
            raise EvaluationNotFound(str(invocation.replay_of_invocation_id))
        self.invocations[invocation.invocation_id] = invocation
        return invocation

    def publish(
        self,
        publication: PipelinePublicationRecord,
        updated_run: PipelineRunRecord,
    ) -> PipelinePublicationRecord:
        if publication.publication_id in self.publications:
            raise DuplicateEvaluationRecord(str(publication.publication_id))
        if updated_run.run_id not in self.runs:
            raise EvaluationNotFound(str(updated_run.run_id))
        self.publications[publication.publication_id] = publication
        self.runs[updated_run.run_id] = updated_run
        return publication

    def list_publications(self, run_id: UUID):
        return tuple(item for item in self.publications.values() if item.run_id == run_id)
