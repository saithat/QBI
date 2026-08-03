"""Source evidence aggregation without annotation mutation."""

from __future__ import annotations

from collections.abc import Callable, Sequence
from datetime import UTC, datetime
from typing import Protocol
from uuid import NAMESPACE_URL, UUID, uuid4, uuid5

from hiveblot_contracts import (
    AdjudicationEvidenceOverlay,
    AnnotationRevision,
    ArtifactRecord,
    ArtifactVisibility,
    CaseArtifactRole,
    CaseSourceContext,
    EvidenceOverlay,
    FieldPathTarget,
    PredictionEvidenceOverlay,
    ReviewerEvidenceOverlay,
    SourceEvidenceWorkbench,
    SpatialAnnotation,
    WorkbenchEvidenceSource,
)

from .errors import ConcurrencyConflict, InvalidEvaluationState
from .service import EvaluationService


class ArtifactLookup(Protocol):
    def get_artifact(self, artifact_id: UUID) -> ArtifactRecord: ...


class SourceContextRepository(Protocol):
    def append_context(
        self,
        context: CaseSourceContext,
        *,
        expected_head_revision_id: UUID | None,
    ) -> CaseSourceContext: ...

    def list_contexts(self, case_id: UUID) -> Sequence[CaseSourceContext]: ...

    def list_context_revisions(
        self,
        case_id: UUID,
        artifact_id: UUID,
        artifact_role: CaseArtifactRole,
    ) -> Sequence[CaseSourceContext]: ...


class EvidenceWorkbenchService:
    def __init__(
        self,
        evaluation: EvaluationService,
        artifacts: ArtifactLookup,
        contexts: SourceContextRepository,
        *,
        clock: Callable[[], datetime] | None = None,
    ) -> None:
        self._evaluation = evaluation
        self._artifacts = artifacts
        self._contexts = contexts
        self._clock = clock or (lambda: datetime.now(UTC))

    def put_source_context(
        self,
        case_id: UUID,
        artifact_id: UUID,
        artifact_role: CaseArtifactRole,
        *,
        expected_head_revision_id: UUID | None,
        caption: str | None,
        nearby_text: str | None,
    ) -> CaseSourceContext:
        case = self._evaluation.get_case(case_id)
        if not any(
            source.artifact_id == artifact_id and source.role is artifact_role
            for source in case.source_artifacts
        ):
            raise InvalidEvaluationState(
                "source context must reference a case artifact association"
            )
        normalized_caption = _optional_text(caption)
        normalized_nearby = _optional_text(nearby_text)
        if normalized_caption is None and normalized_nearby is None:
            raise InvalidEvaluationState("source context requires a caption or nearby text")
        revisions = self._contexts.list_context_revisions(
            case_id,
            artifact_id,
            artifact_role,
        )
        current = revisions[-1] if revisions else None
        current_head = current.context_revision_id if current is not None else None
        if expected_head_revision_id != current_head:
            raise ConcurrencyConflict("source context head changed")
        context = CaseSourceContext(
            context_revision_id=uuid4(),
            case_id=case_id,
            artifact_id=artifact_id,
            artifact_role=artifact_role,
            revision_number=(current.revision_number + 1) if current is not None else 1,
            prior_revision_id=current_head,
            caption=normalized_caption,
            nearby_text=normalized_nearby,
            created_at=self._clock(),
        )
        return self._contexts.append_context(
            context,
            expected_head_revision_id=expected_head_revision_id,
        )

    def list_source_context_revisions(
        self,
        case_id: UUID,
        artifact_id: UUID,
        artifact_role: CaseArtifactRole,
    ) -> Sequence[CaseSourceContext]:
        case = self._evaluation.get_case(case_id)
        if not any(
            source.artifact_id == artifact_id and source.role is artifact_role
            for source in case.source_artifacts
        ):
            raise InvalidEvaluationState(
                "source context must reference a case artifact association"
            )
        return self._contexts.list_context_revisions(case_id, artifact_id, artifact_role)

    def get_workbench(
        self,
        case_id: UUID,
        *,
        accessible_annotation_organization_ids: tuple[UUID, ...] | None = None,
    ) -> SourceEvidenceWorkbench:
        case = self._evaluation.get_case(case_id)
        contexts = {
            (context.artifact_id, context.artifact_role): context
            for context in self._contexts.list_contexts(case_id)
        }
        sources = tuple(
            WorkbenchEvidenceSource(
                artifact=self._artifacts.get_artifact(source.artifact_id),
                artifact_role=source.role,
                page_number=source.page_number,
                caption=(contexts.get((source.artifact_id, source.role)) or _EMPTY_CONTEXT).caption,
                nearby_text=(
                    contexts.get((source.artifact_id, source.role)) or _EMPTY_CONTEXT
                ).nearby_text,
            )
            for source in case.source_artifacts
        )
        source_ids = {source.artifact.artifact_id for source in sources}
        overlays: list[EvidenceOverlay] = [
            PredictionEvidenceOverlay(
                overlay_id=uuid5(
                    NAMESPACE_URL,
                    f"hiveblot:prediction:{prediction.prediction_id}:{evidence.region.region_id}",
                ),
                source_artifact_id=evidence.artifact_id,
                region=evidence.region,
                label=evidence.description,
                linked_field_keys=(evidence.field_path,) if evidence.field_path else (),
                prediction_id=prediction.prediction_id,
            )
            for prediction in self._evaluation.list_predictions(case_id)
            for evidence in prediction.evidence
            if evidence.region is not None and evidence.artifact_id in source_ids
        ]
        for annotation in self._evaluation.list_annotations(case_id):
            if not _is_visible(
                annotation.visibility,
                annotation.organization_id,
                accessible_annotation_organization_ids,
            ):
                continue
            revision = self._evaluation.get_revision(annotation.head_revision_id)
            field_keys = _field_keys_by_region(revision)
            overlays.extend(
                ReviewerEvidenceOverlay(
                    overlay_id=uuid5(
                        NAMESPACE_URL,
                        f"hiveblot:reviewer:{revision.revision_id}:{spatial.spatial_annotation_id}",
                    ),
                    source_artifact_id=spatial.region.source_artifact_id,
                    region=spatial.region,
                    label=spatial.label,
                    linked_field_keys=_linked_keys(spatial, field_keys),
                    annotation_id=annotation.annotation_id,
                    revision_id=revision.revision_id,
                    reviewer_id=annotation.reviewer_id,
                )
                for spatial in revision.spatial_annotations
                if spatial.region.source_artifact_id in source_ids
            )
        for adjudication in self._evaluation.list_adjudications(case_id):
            if not _is_visible(
                adjudication.visibility,
                adjudication.organization_id,
                accessible_annotation_organization_ids,
            ):
                continue
            revision = self._evaluation.get_revision(adjudication.selected_revision_id)
            field_keys = _field_keys_by_region(revision)
            overlays.extend(
                AdjudicationEvidenceOverlay(
                    overlay_id=uuid5(
                        NAMESPACE_URL,
                        f"hiveblot:adjudication:{adjudication.adjudication_id}:"
                        f"{spatial.spatial_annotation_id}",
                    ),
                    source_artifact_id=spatial.region.source_artifact_id,
                    region=spatial.region,
                    label=spatial.label,
                    linked_field_keys=_linked_keys(spatial, field_keys),
                    adjudication_id=adjudication.adjudication_id,
                    revision_id=revision.revision_id,
                )
                for spatial in revision.spatial_annotations
                if spatial.region.source_artifact_id in source_ids
            )
        return SourceEvidenceWorkbench(
            case_id=case.case_id,
            case_version=case.version,
            sources=sources,
            overlays=tuple(overlays),
        )


class _EmptyContext:
    caption: None = None
    nearby_text: None = None


_EMPTY_CONTEXT = _EmptyContext()


def _is_visible(
    visibility: ArtifactVisibility,
    organization_id: UUID | None,
    accessible_organization_ids: tuple[UUID, ...] | None,
) -> bool:
    return (
        accessible_organization_ids is None
        or visibility is ArtifactVisibility.PUBLIC
        or organization_id in accessible_organization_ids
    )


def _field_keys_by_region(revision: AnnotationRevision) -> dict[UUID, set[str]]:
    result: dict[UUID, set[str]] = {}
    for field in revision.field_annotations:
        key = (
            field.target.field_path
            if isinstance(field.target, FieldPathTarget)
            else "entity:"
            + str(field.target.entity_id)
            + (f":{field.target.field_name}" if field.target.field_name else "")
        )
        for region_id in field.evidence_region_ids:
            result.setdefault(region_id, set()).add(key)
    return result


def _linked_keys(
    spatial: SpatialAnnotation,
    values: dict[UUID, set[str]],
) -> tuple[str, ...]:
    spatial_id = spatial.spatial_annotation_id
    region_id = spatial.region.region_id
    return tuple(sorted(values.get(spatial_id, set()) | values.get(region_id, set())))


def _optional_text(value: str | None) -> str | None:
    if value is None:
        return None
    normalized = value.strip()
    return normalized or None
