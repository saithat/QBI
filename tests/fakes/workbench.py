from __future__ import annotations

from uuid import UUID

from hiveblot_contracts import ArtifactRecord, CaseSourceContext
from hiveblot_evaluation import ConcurrencyConflict
from hiveblot_storage import ArtifactNotFound


class InMemoryArtifactLookup:
    def __init__(self, artifacts: tuple[ArtifactRecord, ...]) -> None:
        self.artifacts = {artifact.artifact_id: artifact for artifact in artifacts}

    def get_artifact(self, artifact_id: UUID) -> ArtifactRecord:
        try:
            return self.artifacts[artifact_id]
        except KeyError as exc:
            raise ArtifactNotFound(f"artifact {artifact_id} does not exist") from exc


class InMemorySourceContextRepository:
    def __init__(self) -> None:
        self.contexts: dict[tuple[UUID, UUID, str], list[CaseSourceContext]] = {}

    def append_context(
        self,
        context: CaseSourceContext,
        *,
        expected_head_revision_id: UUID | None,
    ) -> CaseSourceContext:
        key = (context.case_id, context.artifact_id, context.artifact_role.value)
        revisions = self.contexts.setdefault(key, [])
        current_head = revisions[-1].context_revision_id if revisions else None
        if current_head != expected_head_revision_id:
            raise ConcurrencyConflict("source context head changed")
        revisions.append(context)
        return context

    def list_contexts(self, case_id: UUID):
        return tuple(
            revisions[-1]
            for (stored_case_id, _, _), revisions in self.contexts.items()
            if stored_case_id == case_id
        )

    def list_context_revisions(self, case_id, artifact_id, artifact_role):
        return tuple(self.contexts.get((case_id, artifact_id, artifact_role.value), ()))
