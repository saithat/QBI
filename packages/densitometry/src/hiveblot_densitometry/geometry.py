"""Resolve exact prediction or reviewer geometry into densitometry inputs."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from uuid import UUID

from hiveblot_contracts import (
    ArtifactRecord,
    ArtifactReference,
    ArtifactVisibility,
    BoundingRegion,
    CaseArtifactRole,
    DensitometryBandRegion,
    DensitometryConfiguration,
    DensitometryGeometryReference,
    DensitometryImageKind,
    DensitometryInput,
    DensitometryLaneRegion,
    DensitometryTargetRegion,
    ObservationState,
    PredictionGeometryReference,
    ReviewerGeometryReference,
    SpatialAnnotation,
    SpatialAnnotationSet,
    SpatialAnnotationType,
    SpatialEditorRelationship,
    SpatialRelationshipType,
)
from hiveblot_evaluation import EvaluationService, InvalidEvaluationState, SpatialAnnotationService
from hiveblot_storage import ArtifactReader

from .errors import InvalidDensitometryInput


@dataclass(frozen=True, slots=True)
class DensitometryGeometryOption:
    geometry: DensitometryGeometryReference
    label: str
    created_at: datetime
    visibility: ArtifactVisibility
    organization_id: UUID | None
    image_artifact: ArtifactRecord
    image_kind: DensitometryImageKind
    lanes: tuple[DensitometryLaneRegion, ...]
    targets: tuple[DensitometryTargetRegion, ...]
    bands: tuple[DensitometryBandRegion, ...]
    inferred_loading_control_target_ids: tuple[UUID, ...]


class DensitometryGeometryResolver:
    def __init__(
        self,
        evaluation: EvaluationService,
        spatial: SpatialAnnotationService,
        artifacts: ArtifactReader,
    ) -> None:
        self._evaluation = evaluation
        self._spatial = spatial
        self._artifacts = artifacts

    def list_options(self, case_id: UUID) -> tuple[DensitometryGeometryOption, ...]:
        case = self._evaluation.get_case(case_id)
        image_sources = tuple(
            (source, self._artifacts.get_artifact(source.artifact_id))
            for source in case.source_artifacts
            if self._artifacts.get_artifact(source.artifact_id).media_type.startswith("image/")
        )
        snapshots: list[
            tuple[
                DensitometryGeometryReference,
                str,
                datetime,
                ArtifactVisibility,
                UUID | None,
                SpatialAnnotationSet,
            ]
        ] = []
        for prediction in self._evaluation.list_predictions(case_id):
            try:
                _, annotation_set = self._spatial.prediction_set(case_id, prediction.prediction_id)
            except InvalidEvaluationState:
                continue
            snapshots.append(
                (
                    PredictionGeometryReference(prediction_id=prediction.prediction_id),
                    f"Prediction {prediction.producer.name} {prediction.producer.version}",
                    prediction.created_at,
                    case.visibility,
                    case.organization_id,
                    annotation_set,
                )
            )
        for document in self._evaluation.list_annotations(case_id):
            for revision in self._evaluation.list_revisions(document.annotation_id):
                snapshots.append(
                    (
                        ReviewerGeometryReference(annotation_revision_id=revision.revision_id),
                        f"Reviewer revision {revision.revision_number}",
                        revision.created_at,
                        document.visibility,
                        document.organization_id,
                        self._spatial.annotation_set(revision),
                    )
                )
        options: list[DensitometryGeometryOption] = []
        for geometry, label, created_at, visibility, organization_id, annotation_set in snapshots:
            for source, artifact in image_sources:
                try:
                    lanes, targets, bands, controls = _regions(
                        annotation_set,
                        artifact.artifact_id,
                    )
                except InvalidDensitometryInput:
                    continue
                options.append(
                    DensitometryGeometryOption(
                        geometry=geometry,
                        label=label,
                        created_at=created_at,
                        visibility=visibility,
                        organization_id=organization_id,
                        image_artifact=artifact,
                        image_kind=_image_kind(source.role),
                        lanes=lanes,
                        targets=targets,
                        bands=bands,
                        inferred_loading_control_target_ids=controls,
                    )
                )
        return tuple(
            sorted(
                options,
                key=lambda item: (
                    item.created_at,
                    item.geometry.source_type,
                    str(item.image_artifact.artifact_id),
                ),
                reverse=True,
            )
        )

    def build_input(
        self,
        case_id: UUID,
        *,
        image_artifact_id: UUID,
        geometry: DensitometryGeometryReference,
        loading_control_target_id: UUID | None,
        configuration: DensitometryConfiguration,
    ) -> DensitometryInput:
        case = self._evaluation.get_case(case_id)
        source = next(
            (item for item in case.source_artifacts if item.artifact_id == image_artifact_id),
            None,
        )
        if source is None:
            raise InvalidDensitometryInput("densitometry image must belong to the evaluation case")
        artifact = self._artifacts.get_artifact(image_artifact_id)
        if not artifact.media_type.startswith("image/"):
            raise InvalidDensitometryInput("densitometry source must be a raster image artifact")
        annotation_set = self._resolve_set(case_id, geometry)
        lanes, targets, bands, inferred_controls = _regions(annotation_set, image_artifact_id)
        selected_control = loading_control_target_id
        if selected_control is None and len(inferred_controls) == 1:
            selected_control = inferred_controls[0]
        targets = tuple(
            DensitometryTargetRegion(
                target_id=target.target_id,
                label=target.label,
                is_loading_control=(
                    target.target_id in inferred_controls or target.target_id == selected_control
                ),
                region=target.region,
            )
            for target in targets
        )
        return DensitometryInput(
            case_id=case_id,
            image_artifact=_reference(artifact),
            image_kind=_image_kind(source.role),
            geometry=geometry,
            lanes=lanes,
            targets=targets,
            bands=bands,
            loading_control_target_id=selected_control,
            configuration=configuration,
        )

    def _resolve_set(
        self,
        case_id: UUID,
        geometry: DensitometryGeometryReference,
    ) -> SpatialAnnotationSet:
        if isinstance(geometry, PredictionGeometryReference):
            return self._spatial.prediction_set(case_id, geometry.prediction_id)[1]
        if isinstance(geometry, ReviewerGeometryReference):
            revision = self._evaluation.get_revision(geometry.annotation_revision_id)
            document = self._evaluation.get_annotation(revision.annotation_id)
            if document.case_id != case_id:
                raise InvalidDensitometryInput(
                    "reviewer geometry revision must belong to the evaluation case"
                )
            return self._spatial.annotation_set(revision)
        raise InvalidDensitometryInput("unsupported densitometry geometry reference")


def _regions(
    annotation_set: SpatialAnnotationSet,
    image_artifact_id: UUID,
) -> tuple[
    tuple[DensitometryLaneRegion, ...],
    tuple[DensitometryTargetRegion, ...],
    tuple[DensitometryBandRegion, ...],
    tuple[UUID, ...],
]:
    source_annotations = tuple(
        item
        for item in annotation_set.spatial_annotations
        if item.region.source_artifact_id == image_artifact_id
        and item.region.page_number is None
        and item.state is ObservationState.PRESENT
    )
    lane_annotations = _ordered_lanes(
        tuple(
            item
            for item in source_annotations
            if item.annotation_type is SpatialAnnotationType.LANE
        ),
        annotation_set.relationships,
    )
    target_annotations = sorted(
        (
            item
            for item in source_annotations
            if item.annotation_type is SpatialAnnotationType.PROTEIN_ROW
        ),
        key=lambda item: (item.region.y, item.region.x, str(item.spatial_annotation_id)),
    )
    band_annotations = tuple(
        item for item in source_annotations if item.annotation_type is SpatialAnnotationType.BAND
    )
    if not lane_annotations or not target_annotations or not band_annotations:
        raise InvalidDensitometryInput(
            "geometry requires lanes, protein rows, and band regions on one image"
        )
    annotation_by_id = {item.spatial_annotation_id: item for item in source_annotations}
    parent_ids: dict[UUID, set[UUID]] = {}
    for relationship in annotation_set.relationships:
        if relationship.relation_type is not SpatialRelationshipType.CONTAINS:
            continue
        if (
            relationship.subject_id in annotation_by_id
            and relationship.object_id in annotation_by_id
        ):
            parent_ids.setdefault(relationship.object_id, set()).add(relationship.subject_id)
    lanes = tuple(
        DensitometryLaneRegion(
            lane_id=item.spatial_annotation_id,
            lane_index=index,
            label=item.label,
            region=item.region,
        )
        for index, item in enumerate(lane_annotations, start=1)
    )
    control_ids = tuple(
        sorted(
            {
                relationship.object_id
                for relationship in annotation_set.relationships
                if relationship.relation_type is SpatialRelationshipType.TARGET_USES_LOADING_CONTROL
                and relationship.object_id in annotation_by_id
                and annotation_by_id[relationship.object_id].annotation_type
                is SpatialAnnotationType.PROTEIN_ROW
            },
            key=str,
        )
    )
    targets = tuple(
        DensitometryTargetRegion(
            target_id=item.spatial_annotation_id,
            label=item.label or f"target {index}",
            is_loading_control=item.spatial_annotation_id in control_ids,
            region=item.region,
        )
        for index, item in enumerate(target_annotations, start=1)
    )
    lane_by_id = {item.lane_id: item for item in lanes}
    target_by_id = {item.target_id: item for item in targets}
    bands: list[DensitometryBandRegion] = []
    for band in band_annotations:
        parents = parent_ids.get(band.spatial_annotation_id, set())
        lane_candidates = [item for item in lanes if item.lane_id in parents]
        target_candidates = [item for item in targets if item.target_id in parents]
        if not lane_candidates:
            lane_candidates = [item for item in lanes if _contains(item.region, band.region)]
        if not target_candidates:
            target_candidates = [item for item in targets if _contains(item.region, band.region)]
        if len(lane_candidates) != 1 or len(target_candidates) != 1:
            raise InvalidDensitometryInput(
                "every band must map unambiguously to one lane and one protein row"
            )
        lane_id = lane_candidates[0].lane_id
        target_id = target_candidates[0].target_id
        if lane_id not in lane_by_id or target_id not in target_by_id:  # pragma: no cover
            raise AssertionError("resolved densitometry geometry references unknown parents")
        bands.append(
            DensitometryBandRegion(
                band_id=band.spatial_annotation_id,
                lane_id=lane_id,
                target_id=target_id,
                region=band.region,
            )
        )
    pairs = {(item.lane_id, item.target_id) for item in bands}
    expected = {(lane.lane_id, target.target_id) for lane in lanes for target in targets}
    if pairs != expected or len(bands) != len(expected):
        raise InvalidDensitometryInput(
            "geometry requires exactly one band for every lane and protein-row pair"
        )
    return lanes, targets, tuple(bands), control_ids


def _image_kind(role: CaseArtifactRole) -> DensitometryImageKind:
    return (
        DensitometryImageKind.RAW_SOURCE
        if role is CaseArtifactRole.RAW_SOURCE
        else DensitometryImageKind.PUBLICATION_FIGURE
    )


def _reference(artifact: ArtifactRecord) -> ArtifactReference:
    return ArtifactReference(
        artifact_id=artifact.artifact_id,
        sha256=artifact.sha256,
        media_type=artifact.media_type,
        byte_size=artifact.byte_size,
    )


def _contains(parent: BoundingRegion, child: BoundingRegion) -> bool:
    return (
        child.x >= parent.x
        and child.y >= parent.y
        and child.x + child.width <= parent.x + parent.width
        and child.y + child.height <= parent.y + parent.height
    )


def _ordered_lanes(
    lanes: tuple[SpatialAnnotation, ...],
    relationships: tuple[SpatialEditorRelationship, ...],
) -> tuple[SpatialAnnotation, ...]:
    by_id = {item.spatial_annotation_id: item for item in lanes}
    outgoing: dict[UUID, list[UUID]] = {lane_id: [] for lane_id in by_id}
    incoming: dict[UUID, int] = {lane_id: 0 for lane_id in by_id}
    for relationship in relationships:
        if (
            relationship.relation_type is SpatialRelationshipType.PRECEDES
            and relationship.subject_id in by_id
            and relationship.object_id in by_id
        ):
            outgoing[relationship.subject_id].append(relationship.object_id)
            incoming[relationship.object_id] += 1

    def sort_key(lane_id: UUID) -> tuple[float, float, str]:
        lane = by_id[lane_id]
        return lane.region.x, lane.region.y, str(lane_id)

    available = sorted(
        (lane_id for lane_id, count in incoming.items() if count == 0),
        key=sort_key,
    )
    ordered: list[SpatialAnnotation] = []
    while available:
        lane_id = available.pop(0)
        ordered.append(by_id[lane_id])
        for following_id in outgoing[lane_id]:
            incoming[following_id] -= 1
            if incoming[following_id] == 0:
                available.append(following_id)
                available.sort(key=sort_key)
    if len(ordered) != len(lanes):  # pragma: no cover - SpatialAnnotationSet rejects cycles
        raise InvalidDensitometryInput("lane-order relationships contain a cycle")
    return tuple(ordered)
