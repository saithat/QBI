"""Deterministic conversion of strict model predictions into reviewable case output."""

from __future__ import annotations

from uuid import NAMESPACE_URL, UUID, uuid5

from hiveblot_contracts import (
    BiologicalContextAnnotation,
    BiologicalContextType,
    BoundingRegion,
    LaneConditionAnnotation,
    ModelBandState,
    ModelConfidence,
    ObservationState,
    ProteinAnnotation,
    ProteinRole,
    ScientificRelationship,
    ScientificRelationshipType,
    SpatialAnnotation,
    SpatialAnnotationSet,
    SpatialAnnotationType,
    SpatialEditorRelationship,
    SpatialRelationshipType,
    TreatmentAnnotation,
    ValidationIssue,
    ValidationSeverity,
    WesternBlotCandidatePrediction,
    WesternBlotCandidatePredictionSet,
    WesternBlotExtractionImplementation,
    WesternBlotExtractionResult,
    WesternBlotFieldEvidence,
    WesternBlotPanelPrediction,
    WesternBlotStructuredAnnotation,
)


def assemble_extraction_result(
    *,
    case_id: UUID,
    implementation: WesternBlotExtractionImplementation,
    predictions: WesternBlotCandidatePredictionSet,
) -> WesternBlotExtractionResult:
    """Create stable entity/geometry identities without inventing pixel measurements."""

    source = predictions.figure_candidates.source_artifact
    candidates = {
        candidate.candidate_id: candidate for candidate in predictions.figure_candidates.candidates
    }
    spatial_annotations: list[SpatialAnnotation] = []
    spatial_relationships: list[SpatialEditorRelationship] = []
    proteins: list[ProteinAnnotation] = []
    lanes: list[LaneConditionAnnotation] = []
    contexts: list[BiologicalContextAnnotation] = []
    treatments: list[TreatmentAnnotation] = []
    scientific_relationships: list[ScientificRelationship] = []
    field_evidence: list[WesternBlotFieldEvidence] = []
    validation_issues: list[ValidationIssue] = []
    confidence_values: list[float] = []
    positive_count = 0

    for candidate_index, prediction in enumerate(predictions.predictions):
        candidate = candidates[prediction.candidate_id]
        figure_region_id = _stable_id(prediction.candidate_id, "region:figure")
        figure_region = _copy_region(candidate.region, figure_region_id)
        spatial_annotations.append(
            SpatialAnnotation(
                spatial_annotation_id=figure_region_id,
                annotation_type=SpatialAnnotationType.FIGURE,
                state=ObservationState.PRESENT,
                region=figure_region,
                label=prediction.figure_label or f"figure candidate {candidate_index + 1}",
            )
        )
        candidate_path = f"/candidate_predictions/predictions/{candidate_index}"
        _append_region_evidence(
            field_evidence,
            region_id=figure_region_id,
            base_path=candidate_path,
            values=(
                ("is_western_blot", prediction.is_western_blot),
                ("reason", prediction.reason),
                ("figure_label", prediction.figure_label),
                ("figure_caption", prediction.figure_caption),
                ("biological_sample", prediction.biological_sample),
                ("cell_line_tissue", prediction.cell_line_tissue),
                ("organism", prediction.organism),
                ("sample_type", prediction.sample_type),
            ),
        )
        for warning_index, warning in enumerate(prediction.warnings):
            validation_issues.append(
                _issue(
                    source.artifact_id,
                    prediction.candidate_id,
                    f"model_candidate_warning_{warning_index + 1}",
                    warning,
                    f"{candidate_path}/warnings/{warning_index}",
                )
            )
        if not prediction.is_western_blot:
            continue
        positive_count += 1
        candidate_context_ids = _append_contexts(
            prediction,
            figure_region_id=figure_region_id,
            contexts=contexts,
            field_evidence=field_evidence,
        )
        if prediction.figure_caption is None:
            field_evidence.append(
                WesternBlotFieldEvidence(
                    field_path=f"/candidate_predictions/predictions/{candidate_index}/figure_caption",
                    missing_reason="model did not extract a figure caption",
                )
            )
            validation_issues.append(
                _issue(
                    source.artifact_id,
                    prediction.candidate_id,
                    "missing_figure_caption",
                    "The positive figure candidate has no extracted caption.",
                    f"/candidate_predictions/predictions/{candidate_index}/figure_caption",
                )
            )

        for panel_index, panel in enumerate(prediction.panels):
            panel_region = _vertical_slice(candidate.region, panel_index, len(prediction.panels))
            panel_region_id = _stable_id(panel.panel_id, "region:panel")
            panel_region = _copy_region(panel_region, panel_region_id)
            blot_region_id = _stable_id(panel.panel_id, "region:blot")
            blot_region = _copy_region(panel_region, blot_region_id)
            spatial_annotations.extend(
                (
                    SpatialAnnotation(
                        spatial_annotation_id=panel_region_id,
                        annotation_type=SpatialAnnotationType.PANEL,
                        state=ObservationState.PRESENT,
                        region=panel_region,
                        label=panel.panel_label or f"panel {panel_index + 1}",
                    ),
                    SpatialAnnotation(
                        spatial_annotation_id=blot_region_id,
                        annotation_type=SpatialAnnotationType.BLOT,
                        state=ObservationState.PRESENT,
                        region=blot_region,
                        label=panel.panel_title or panel.panel_label,
                    ),
                )
            )
            spatial_relationships.extend(
                (
                    _spatial_edge(
                        panel.panel_id,
                        "figure-panel",
                        figure_region_id,
                        SpatialRelationshipType.CONTAINS,
                        panel_region_id,
                    ),
                    _spatial_edge(
                        panel.panel_id,
                        "panel-blot",
                        panel_region_id,
                        SpatialRelationshipType.CONTAINS,
                        blot_region_id,
                    ),
                )
            )
            panel_path = f"{candidate_path}/panels/{panel_index}"
            _append_region_evidence(
                field_evidence,
                region_id=panel_region_id,
                base_path=panel_path,
                values=(
                    ("panel_label", panel.panel_label),
                    ("panel_title", panel.panel_title),
                    ("treatment_context", panel.treatment_context),
                ),
            )
            panel_treatment_id = _append_treatment(
                panel,
                panel_region_id=panel_region_id,
                treatments=treatments,
                field_evidence=field_evidence,
            )
            _append_panel_graph(
                panel,
                blot_region=blot_region,
                blot_region_id=blot_region_id,
                context_ids=candidate_context_ids,
                treatment_id=panel_treatment_id,
                spatial_annotations=spatial_annotations,
                spatial_relationships=spatial_relationships,
                proteins=proteins,
                lanes=lanes,
                scientific_relationships=scientific_relationships,
                field_evidence=field_evidence,
                confidence_values=confidence_values,
                prediction_index=candidate_index,
                panel_index=panel_index,
            )
            for warning_index, warning in enumerate(panel.warnings):
                validation_issues.append(
                    _issue(
                        source.artifact_id,
                        panel.panel_id,
                        f"model_panel_warning_{warning_index + 1}",
                        warning,
                        f"{panel_path}/warnings/{warning_index}",
                    )
                )

    if positive_count:
        validation_issues.append(
            _issue(
                source.artifact_id,
                case_id,
                "estimated_geometry",
                "Panel, blot, lane, row, and band subdivisions are deterministic estimates "
                "within the detected figure candidate and require reviewer validation.",
                "/spatial_annotation_set",
            )
        )
    else:
        field_evidence.append(
            WesternBlotFieldEvidence(
                field_path="/structured_annotation",
                missing_reason="no detected candidate was classified as a western blot",
            )
        )
        validation_issues.append(
            _issue(
                source.artifact_id,
                case_id,
                "no_western_blot_detected",
                "No detected figure candidate was classified as a western blot.",
                "/structured_annotation",
            )
        )

    structured = WesternBlotStructuredAnnotation(
        proteins=tuple(proteins),
        biological_contexts=tuple(contexts),
        treatments=tuple(treatments),
        lane_conditions=tuple(lanes),
        relationships=tuple(scientific_relationships),
    )
    spatial = SpatialAnnotationSet(
        spatial_annotations=tuple(spatial_annotations),
        relationships=tuple(spatial_relationships),
    )
    confidence = (
        float(sum(confidence_values) / len(confidence_values)) if confidence_values else None
    )
    return WesternBlotExtractionResult(
        case_id=case_id,
        source_artifact=source,
        implementation=implementation,
        candidate_predictions=predictions,
        structured_annotation=structured,
        spatial_annotation_set=spatial,
        field_evidence=tuple(field_evidence),
        validation_issues=tuple(validation_issues),
        confidence=confidence,
    )


def _append_contexts(
    prediction: WesternBlotCandidatePrediction,
    *,
    figure_region_id: UUID,
    contexts: list[BiologicalContextAnnotation],
    field_evidence: list[WesternBlotFieldEvidence],
) -> tuple[UUID, ...]:
    values = (
        ("cell_line_tissue", BiologicalContextType.CELL_LINE, prediction.cell_line_tissue),
        ("biological_sample", BiologicalContextType.TISSUE, prediction.biological_sample),
        ("sample_type", BiologicalContextType.TISSUE, prediction.sample_type),
        ("organism", BiologicalContextType.ORGANISM, prediction.organism),
    )
    ids: list[UUID] = []
    for key, context_type, value in values:
        if value is None:
            continue
        context_id = _stable_id(prediction.candidate_id, f"context:{key}:{value}")
        ids.append(context_id)
        index = len(contexts)
        contexts.append(
            BiologicalContextAnnotation(
                entity_id=context_id,
                context_type=context_type,
                name=value,
                original_extracted_text=value,
                evidence_region_ids=(figure_region_id,),
            )
        )
        field_evidence.append(
            WesternBlotFieldEvidence(
                field_path=f"/structured_annotation/biological_contexts/{index}/name",
                region_ids=(figure_region_id,),
            )
        )
    return tuple(ids)


def _append_treatment(
    panel: WesternBlotPanelPrediction,
    *,
    panel_region_id: UUID,
    treatments: list[TreatmentAnnotation],
    field_evidence: list[WesternBlotFieldEvidence],
) -> UUID | None:
    if panel.treatment_context is None:
        return None
    treatment_id = _stable_id(panel.panel_id, f"treatment:{panel.treatment_context}")
    index = len(treatments)
    treatments.append(
        TreatmentAnnotation(
            entity_id=treatment_id,
            name=panel.treatment_context,
            original_extracted_text=panel.treatment_context,
            evidence_region_ids=(panel_region_id,),
        )
    )
    field_evidence.append(
        WesternBlotFieldEvidence(
            field_path=f"/structured_annotation/treatments/{index}/name",
            region_ids=(panel_region_id,),
        )
    )
    return treatment_id


def _append_panel_graph(
    panel: WesternBlotPanelPrediction,
    *,
    blot_region: BoundingRegion,
    blot_region_id: UUID,
    context_ids: tuple[UUID, ...],
    treatment_id: UUID | None,
    spatial_annotations: list[SpatialAnnotation],
    spatial_relationships: list[SpatialEditorRelationship],
    proteins: list[ProteinAnnotation],
    lanes: list[LaneConditionAnnotation],
    scientific_relationships: list[ScientificRelationship],
    field_evidence: list[WesternBlotFieldEvidence],
    confidence_values: list[float],
    prediction_index: int,
    panel_index: int,
) -> None:
    row_regions: dict[int, UUID] = {}
    lane_regions: dict[int, UUID] = {}
    protein_by_row: dict[int, UUID] = {}
    lane_by_index: dict[int, UUID] = {}
    loading_control_rows: list[int] = []

    for row_position, target in enumerate(panel.targets):
        region_id = _stable_id(target.target_id, "region:protein-row")
        row_region = _copy_region(
            _vertical_slice(blot_region, row_position, len(panel.targets)),
            region_id,
        )
        row_regions[target.row_index] = region_id
        protein_by_row[target.row_index] = target.target_id
        if target.is_loading_control:
            loading_control_rows.append(target.row_index)
        spatial_annotations.append(
            SpatialAnnotation(
                spatial_annotation_id=region_id,
                annotation_type=SpatialAnnotationType.PROTEIN_ROW,
                state=ObservationState.PRESENT,
                region=row_region,
                label=(
                    f"loading control {target.target}"
                    if target.is_loading_control
                    else target.target
                ),
            )
        )
        spatial_relationships.append(
            _spatial_edge(
                target.target_id,
                "blot-row",
                blot_region_id,
                SpatialRelationshipType.CONTAINS,
                region_id,
            )
        )
        protein_index = len(proteins)
        proteins.append(
            ProteinAnnotation(
                entity_id=target.target_id,
                role=(
                    ProteinRole.LOADING_CONTROL if target.is_loading_control else ProteinRole.TARGET
                ),
                name=target.target,
                original_extracted_text=target.target,
                evidence_region_ids=(region_id,),
            )
        )
        field_evidence.append(
            WesternBlotFieldEvidence(
                field_path=f"/structured_annotation/proteins/{protein_index}/name",
                region_ids=(region_id,),
            )
        )
        _append_region_evidence(
            field_evidence,
            region_id=region_id,
            base_path=(
                f"/candidate_predictions/predictions/{prediction_index}/panels/{panel_index}"
                f"/targets/{row_position}"
            ),
            values=(
                ("row_index", target.row_index),
                ("target", target.target),
                ("is_loading_control", target.is_loading_control),
                ("confidence", target.confidence),
            ),
        )
        confidence_values.append(_confidence_value(target.confidence))

    for lane_position, lane in enumerate(panel.lanes):
        region_id = _stable_id(lane.lane_id, "region:lane")
        lane_region = _copy_region(
            _horizontal_slice(blot_region, lane_position, len(panel.lanes)),
            region_id,
        )
        lane_regions[lane.lane_index] = region_id
        lane_by_index[lane.lane_index] = lane.lane_id
        spatial_annotations.append(
            SpatialAnnotation(
                spatial_annotation_id=region_id,
                annotation_type=SpatialAnnotationType.LANE,
                state=ObservationState.PRESENT,
                region=lane_region,
                label=lane.condition or f"lane {lane.lane_index}",
            )
        )
        spatial_relationships.append(
            _spatial_edge(
                lane.lane_id,
                "blot-lane",
                blot_region_id,
                SpatialRelationshipType.CONTAINS,
                region_id,
            )
        )
        if lane_position:
            previous = panel.lanes[lane_position - 1]
            spatial_relationships.append(
                _spatial_edge(
                    lane.lane_id,
                    "lane-order",
                    lane_regions[previous.lane_index],
                    SpatialRelationshipType.PRECEDES,
                    region_id,
                )
            )
        lane_index = len(lanes)
        lanes.append(
            LaneConditionAnnotation(
                entity_id=lane.lane_id,
                lane_index=lane.lane_index,
                lane_label=f"lane {lane.lane_index}",
                condition_label=lane.condition,
                original_extracted_text=lane.condition,
                evidence_region_ids=(region_id,),
            )
        )
        field_evidence.append(
            WesternBlotFieldEvidence(
                field_path=f"/structured_annotation/lane_conditions/{lane_index}/condition_label",
                region_ids=(region_id,),
                missing_reason=(
                    "lane condition was not visible" if lane.condition is None else None
                ),
            )
        )
        _append_region_evidence(
            field_evidence,
            region_id=region_id,
            base_path=(
                f"/candidate_predictions/predictions/{prediction_index}/panels/{panel_index}"
                f"/lanes/{lane_position}"
            ),
            values=(
                ("lane_index", lane.lane_index),
                ("condition", lane.condition),
                ("confidence", lane.confidence),
            ),
        )
        confidence_values.append(_confidence_value(lane.confidence))
        for context_id in context_ids:
            scientific_relationships.append(
                _scientific_edge(
                    lane.lane_id,
                    f"lane-context:{context_id}",
                    lane.lane_id,
                    ScientificRelationshipType.LANE_USES_BIOLOGICAL_CONTEXT,
                    context_id,
                )
            )
        if treatment_id is not None:
            scientific_relationships.append(
                _scientific_edge(
                    lane.lane_id,
                    f"lane-treatment:{treatment_id}",
                    lane.lane_id,
                    ScientificRelationshipType.LANE_RECEIVES_TREATMENT,
                    treatment_id,
                )
            )

    for band_position, band in enumerate(panel.bands):
        lane_region = next(
            item.region
            for item in spatial_annotations
            if item.spatial_annotation_id == lane_regions[band.lane_index]
        )
        row_region = next(
            item.region
            for item in spatial_annotations
            if item.spatial_annotation_id == row_regions[band.row_index]
        )
        band_region = _intersection(lane_region, row_region, band.band_id)
        spatial_annotations.append(
            SpatialAnnotation(
                spatial_annotation_id=band.band_id,
                annotation_type=SpatialAnnotationType.BAND,
                state=_band_state(band.band_state),
                region=band_region,
                label=f"{band.target}; lane {band.lane_index}",
            )
        )
        spatial_relationships.extend(
            (
                _spatial_edge(
                    band.band_id,
                    "lane-band",
                    lane_regions[band.lane_index],
                    SpatialRelationshipType.CONTAINS,
                    band.band_id,
                ),
                _spatial_edge(
                    band.band_id,
                    "row-band",
                    row_regions[band.row_index],
                    SpatialRelationshipType.CONTAINS,
                    band.band_id,
                ),
            )
        )
        _append_region_evidence(
            field_evidence,
            region_id=band.band_id,
            base_path=(
                f"/candidate_predictions/predictions/{prediction_index}/panels/{panel_index}"
                f"/bands/{band_position}"
            ),
            values=(
                ("row_index", band.row_index),
                ("target", band.target),
                ("lane_index", band.lane_index),
                ("band_state", band.band_state),
                ("confidence", band.confidence),
            ),
        )
        confidence_values.append(_confidence_value(band.confidence))

    for lane in panel.lanes:
        for target in panel.targets:
            scientific_relationships.append(
                _scientific_edge(
                    panel.panel_id,
                    f"lane-protein:{lane.lane_index}:{target.row_index}",
                    lane_by_index[lane.lane_index],
                    ScientificRelationshipType.LANE_CONTAINS_PROTEIN,
                    protein_by_row[target.row_index],
                )
            )
    for target in panel.targets:
        if target.is_loading_control:
            continue
        for loading_row in loading_control_rows:
            control_id = protein_by_row[loading_row]
            scientific_relationships.append(
                _scientific_edge(
                    panel.panel_id,
                    f"protein-control:{target.row_index}:{loading_row}",
                    target.target_id,
                    ScientificRelationshipType.PROTEIN_USES_LOADING_CONTROL,
                    control_id,
                )
            )
            spatial_relationships.append(
                _spatial_edge(
                    panel.panel_id,
                    f"row-control:{target.row_index}:{loading_row}",
                    row_regions[target.row_index],
                    SpatialRelationshipType.TARGET_USES_LOADING_CONTROL,
                    row_regions[loading_row],
                )
            )


def _copy_region(value: BoundingRegion, region_id: UUID) -> BoundingRegion:
    return BoundingRegion(
        region_id=region_id,
        source_artifact_id=value.source_artifact_id,
        x=float(value.x),
        y=float(value.y),
        width=float(value.width),
        height=float(value.height),
        canvas_width=value.canvas_width,
        canvas_height=value.canvas_height,
        page_number=value.page_number,
    )


def _append_region_evidence(
    field_evidence: list[WesternBlotFieldEvidence],
    *,
    region_id: UUID,
    base_path: str,
    values: tuple[tuple[str, object | None], ...],
) -> None:
    for field_name, value in values:
        if value is None:
            continue
        field_evidence.append(
            WesternBlotFieldEvidence(
                field_path=f"{base_path}/{field_name}",
                region_ids=(region_id,),
            )
        )


def _vertical_slice(value: BoundingRegion, index: int, count: int) -> BoundingRegion:
    height = value.height / count
    y = value.y + height * index
    if index == count - 1:
        height = value.y + value.height - y
    return BoundingRegion(
        region_id=value.region_id,
        source_artifact_id=value.source_artifact_id,
        x=float(value.x),
        y=float(y),
        width=float(value.width),
        height=float(height),
        canvas_width=value.canvas_width,
        canvas_height=value.canvas_height,
        page_number=value.page_number,
    )


def _horizontal_slice(value: BoundingRegion, index: int, count: int) -> BoundingRegion:
    width = value.width / count
    x = value.x + width * index
    if index == count - 1:
        width = value.x + value.width - x
    return BoundingRegion(
        region_id=value.region_id,
        source_artifact_id=value.source_artifact_id,
        x=float(x),
        y=float(value.y),
        width=float(width),
        height=float(value.height),
        canvas_width=value.canvas_width,
        canvas_height=value.canvas_height,
        page_number=value.page_number,
    )


def _intersection(lane: BoundingRegion, row: BoundingRegion, region_id: UUID) -> BoundingRegion:
    x = max(lane.x, row.x)
    y = max(lane.y, row.y)
    width = min(lane.x + lane.width, row.x + row.width) - x
    height = min(lane.y + lane.height, row.y + row.height) - y
    return BoundingRegion(
        region_id=region_id,
        source_artifact_id=lane.source_artifact_id,
        x=float(x),
        y=float(y),
        width=float(width),
        height=float(height),
        canvas_width=lane.canvas_width,
        canvas_height=lane.canvas_height,
        page_number=lane.page_number,
    )


def _spatial_edge(
    namespace: UUID,
    key: str,
    subject_id: UUID,
    relation_type: SpatialRelationshipType,
    object_id: UUID,
) -> SpatialEditorRelationship:
    return SpatialEditorRelationship(
        relationship_id=_stable_id(namespace, f"spatial-edge:{key}"),
        subject_id=subject_id,
        relation_type=relation_type,
        object_id=object_id,
    )


def _scientific_edge(
    namespace: UUID,
    key: str,
    subject_id: UUID,
    relation_type: ScientificRelationshipType,
    object_id: UUID,
) -> ScientificRelationship:
    return ScientificRelationship(
        relationship_id=_stable_id(namespace, f"scientific-edge:{key}"),
        subject_id=subject_id,
        relation_type=relation_type,
        object_id=object_id,
    )


def _issue(
    artifact_id: UUID,
    namespace: UUID,
    code: str,
    message: str,
    field_path: str | None,
) -> ValidationIssue:
    return ValidationIssue(
        issue_id=_stable_id(namespace, f"issue:{code}:{field_path or ''}:{message}"),
        severity=ValidationSeverity.WARNING,
        code=code,
        message=message,
        field_path=field_path,
        evidence_artifact_ids=(artifact_id,),
    )


def _band_state(value: ModelBandState) -> ObservationState:
    return {
        ModelBandState.PRESENT: ObservationState.PRESENT,
        ModelBandState.ABSENT: ObservationState.ABSENT,
        ModelBandState.UNCERTAIN: ObservationState.AMBIGUOUS,
    }[value]


def _confidence_value(value: ModelConfidence) -> float:
    return {
        ModelConfidence.LOW: 0.3,
        ModelConfidence.MEDIUM: 0.6,
        ModelConfidence.HIGH: 0.9,
    }[value]


def _stable_id(parent_id: UUID, key: str) -> UUID:
    return uuid5(NAMESPACE_URL, f"urn:hiveblot:western-blot:{parent_id}:{key}")
