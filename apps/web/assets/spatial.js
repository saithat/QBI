"use strict";

const caseId = window.location.pathname.split("/").filter(Boolean).at(-1);
const reviewerId = localReviewerId();
const viewer = document.querySelector("#viewer");
const canvasTransform = document.querySelector("#canvas-transform");
const visual = document.querySelector("#visual");
const sourceImage = document.querySelector("#source-image");
const sourcePdf = document.querySelector("#source-pdf");
const layer = document.querySelector("#spatial-layer");
const viewerMessage = document.querySelector("#viewer-message");
const saveStatus = document.querySelector("#save-status");
const notice = document.querySelector("#editor-notice");
const predictionSelect = document.querySelector("#prediction-select");
const rationale = document.querySelector("#rationale");
const REGION_TYPES = ["figure", "panel", "blot", "lane", "protein_row", "band", "label", "quantification_plot"];
const STATES = ["present", "absent", "unknown", "ambiguous", "not_applicable"];
const RELATION_TYPES = ["contains", "labels", "grouped_with", "precedes", "target_uses_loading_control"];
const TYPE_RANK = { figure: 0, panel: 1, blot: 2, lane: 3, protein_row: 3, quantification_plot: 3, band: 4, label: 4 };

const state = {
  workbench: null,
  editor: null,
  draft: null,
  sourceIndex: -1,
  selected: new Set(),
  primary: null,
  mode: "select",
  drag: null,
  canvas: { width: 1, height: 1 },
  transforms: new Map(),
  documentId: null,
  headRevisionId: null,
  selectedPredictionId: null,
  mutationVersion: 0,
  savedVersion: 0,
  saving: false,
  saveTimer: null,
};

document.querySelector("#case-id").textContent = caseId;
document.querySelector("#reviewer-id").textContent = reviewerId;
document.querySelector("#evidence-link").href = `/workbench/${caseId}`;
document.querySelector("#annotation-link").href = `/annotate/${caseId}`;
document.querySelector("#save-now").addEventListener("click", () => saveNow());
document.querySelector("#accept-all").addEventListener("click", () => acceptPrediction([], true));
document.querySelector("#delete-region").addEventListener("click", deleteSelected);
document.querySelector("#split-region").addEventListener("click", splitSelected);
document.querySelector("#group-regions").addEventListener("click", groupSelected);
document.querySelector("#add-relationship").addEventListener("click", addRelationship);
document.querySelector("#relationship-type").addEventListener("change", renderRelationshipBuilder);
document.querySelector("#zoom-in").addEventListener("click", () => zoomBy(1.2));
document.querySelector("#zoom-out").addEventListener("click", () => zoomBy(1 / 1.2));
document.querySelector("#reset-view").addEventListener("click", resetView);
document.querySelector("#rotate-left").addEventListener("click", () => rotateBy(-90));
document.querySelector("#rotate-right").addEventListener("click", () => rotateBy(90));
document.querySelector("#fullscreen").addEventListener("click", toggleFullscreen);
document.querySelector("#show-prediction").addEventListener("change", renderRegions);
document.querySelector("#show-reviewer").addEventListener("change", renderRegions);
document.querySelectorAll(".mode").forEach((button) => button.addEventListener("click", () => setMode(button.dataset.mode)));
predictionSelect.addEventListener("change", changePrediction);
rationale.addEventListener("input", () => scheduleSave());
viewer.addEventListener("wheel", onWheel, { passive: false });
viewer.addEventListener("pointerdown", beginPan);
viewer.addEventListener("pointermove", continuePan);
viewer.addEventListener("pointerup", endPan);
viewer.addEventListener("pointercancel", endPan);
layer.addEventListener("pointerdown", beginLayerGesture);
layer.addEventListener("pointermove", continueLayerGesture);
layer.addEventListener("pointerup", endLayerGesture);
layer.addEventListener("pointercancel", endLayerGesture);
layer.addEventListener("pointermove", updateCoordinateStatus);
document.addEventListener("keydown", handleShortcut);
window.addEventListener("resize", applyTransform);

loadEditor();

async function loadEditor(predictionId = null) {
  setSaveStatus("Loading…");
  hideNotice();
  const params = new URLSearchParams({ reviewer_id: reviewerId });
  if (predictionId) params.set("prediction_id", predictionId);
  try {
    const [workbenchResponse, editorResponse] = await Promise.all([
      fetch(`/api/v1/evaluation-cases/${caseId}/workbench`),
      fetch(`/api/v1/evaluation-cases/${caseId}/spatial-editor?${params}`),
    ]);
    if (!workbenchResponse.ok) throw new Error(await responseMessage(workbenchResponse));
    if (!editorResponse.ok) throw new Error(await responseMessage(editorResponse));
    state.workbench = await workbenchResponse.json();
    state.editor = await editorResponse.json();
    state.draft = deepCopy(state.editor.draft_set);
    state.documentId = state.editor.annotation_document?.annotation_id || null;
    state.headRevisionId = state.editor.head_revision?.revision_id || null;
    state.selectedPredictionId = state.editor.selected_prediction_id;
    state.savedVersion = state.mutationVersion;
    rationale.value = state.editor.head_revision?.rationale || "";
    renderAll();
    const preferred = state.workbench.sources.findIndex((source) => source.artifact.media_type.startsWith("image/"));
    await selectSource(preferred >= 0 ? preferred : 0);
    setSaveStatus(state.headRevisionId ? `Saved revision ${state.editor.head_revision.revision_number}` : "New review");
  } catch (error) {
    showNotice(error.message || "The spatial editor could not be loaded.");
    showViewerMessage("Source geometry could not be loaded.", true);
    setSaveStatus("Load failed", true);
  }
}

function renderAll() {
  renderSources();
  renderPredictionOptions();
  renderPredictionList();
  renderRegions();
  renderInspector();
  renderLanes();
  renderRelationshipBuilder();
  renderRelationships();
  renderDiff();
  renderErrorCodes();
  renderRevisions();
}

function renderSources() {
  const container = document.querySelector("#source-list");
  container.replaceChildren();
  state.workbench.sources.forEach((source, index) => {
    const button = document.createElement("button");
    button.type = "button";
    button.className = "source-card";
    button.classList.toggle("active", index === state.sourceIndex);
    const name = document.createElement("strong");
    name.textContent = source.artifact.original_filename;
    const meta = document.createElement("span");
    meta.textContent = `${humanize(source.artifact_role)} · ${source.artifact.media_type}`;
    button.append(name, meta);
    button.addEventListener("click", () => selectSource(index));
    container.append(button);
  });
}

async function selectSource(index) {
  if (!state.workbench.sources.length) {
    showViewerMessage("This case has no source artifacts.", true);
    return;
  }
  saveTransform();
  state.sourceIndex = Math.max(0, index);
  state.selected.clear();
  state.primary = null;
  renderSources();
  restoreTransform(currentSource().artifact.artifact_id);
  await loadSource(currentSource());
  renderInspector();
  renderLanes();
  renderRelationshipBuilder();
}

async function loadSource(source) {
  showViewerMessage("Requesting expiring source access…");
  document.querySelector("#load-status").textContent = "Loading directly from object storage";
  sourceImage.hidden = true;
  sourcePdf.hidden = true;
  layer.hidden = true;
  sourceImage.removeAttribute("src");
  sourcePdf.removeAttribute("src");
  try {
    const response = await fetch(`/api/v1/artifacts/${source.artifact.artifact_id}/download-url`, { method: "POST" });
    if (!response.ok) throw new Error(await responseMessage(response));
    const access = await response.json();
    if (source.artifact.media_type.startsWith("image/")) {
      sourceImage.hidden = false;
      sourceImage.onload = () => {
        configureCanvas(sourceImage.naturalWidth, sourceImage.naturalHeight);
        revealMedia();
        layer.hidden = false;
        renderRegions();
      };
      sourceImage.onerror = () => showViewerMessage("The signed image could not be decoded.", true);
      sourceImage.src = access.url;
    } else if (source.artifact.media_type === "application/pdf") {
      sourcePdf.hidden = false;
      sourcePdf.src = `${access.url}#page=${source.page_number || 1}&zoom=page-width`;
      revealMedia();
      showNotice("Native PDF viewing is read-only. Edit geometry on an image or page-render artifact.");
    } else {
      showViewerMessage(`Geometry editing is unavailable for ${source.artifact.media_type}.`, true);
    }
  } catch (error) {
    showViewerMessage(error.message || "The source artifact is unavailable.", true);
  }
}

function configureCanvas(naturalWidth, naturalHeight) {
  const source = currentSource();
  const regions = [...reviewerRegions(), ...predictionRegions()].filter((item) => regionMatchesSource(item, source));
  const reference = regions[0]?.region;
  state.canvas = { width: reference?.canvas_width || naturalWidth, height: reference?.canvas_height || naturalHeight };
  layer.setAttribute("viewBox", `0 0 ${state.canvas.width} ${state.canvas.height}`);
}

function revealMedia() {
  viewerMessage.hidden = true;
  canvasTransform.hidden = false;
  document.querySelector("#load-status").textContent = "Streaming from signed object URL";
  applyTransform();
}

function showViewerMessage(message, error = false) {
  canvasTransform.hidden = true;
  viewerMessage.hidden = false;
  viewerMessage.textContent = message;
  viewerMessage.classList.toggle("error", error);
}

function renderPredictionOptions() {
  predictionSelect.replaceChildren();
  if (!state.editor.predictions.length) {
    predictionSelect.append(new Option("No predictions", ""));
    predictionSelect.disabled = true;
    return;
  }
  predictionSelect.disabled = false;
  state.editor.predictions.forEach((prediction) => {
    const unavailable = prediction.spatial_output_available ? "" : " · no regions";
    const option = new Option(`${prediction.producer_name} ${prediction.producer_version}${unavailable}`, prediction.prediction_id);
    option.disabled = !prediction.spatial_output_available;
    option.selected = prediction.prediction_id === state.selectedPredictionId;
    predictionSelect.append(option);
  });
}

function renderPredictionList() {
  const container = document.querySelector("#prediction-list");
  container.replaceChildren();
  const regions = predictionRegions();
  document.querySelector("#accept-all").disabled = !regions.length;
  if (!regions.length) {
    container.append(compactMessage("No drawable predicted regions."));
    return;
  }
  regions.forEach((annotation) => {
    const card = compactCard(regionName(annotation), humanize(annotation.annotation_type));
    const accept = document.createElement("button");
    accept.type = "button";
    accept.textContent = "Accept";
    accept.addEventListener("click", () => acceptPrediction([annotation.spatial_annotation_id], false));
    card.append(accept);
    container.append(card);
  });
}

function renderRegions() {
  if (!state.draft || !currentSource()) return;
  layer.replaceChildren();
  if (document.querySelector("#show-prediction").checked) {
    predictionRegions().filter((item) => regionMatchesSource(item, currentSource())).forEach((annotation) => {
      layer.append(regionRect(annotation, "prediction"));
    });
  }
  if (document.querySelector("#show-reviewer").checked) {
    reviewerRegions().filter((item) => regionMatchesSource(item, currentSource())).forEach((annotation) => {
      const rect = regionRect(annotation, "reviewer");
      rect.classList.toggle("selected", annotation.spatial_annotation_id === state.primary);
      rect.classList.toggle("multi-selected", state.selected.has(annotation.spatial_annotation_id));
      rect.addEventListener("pointerdown", (event) => beginRegionMove(event, annotation));
      layer.append(rect);
      if (annotation.label) layer.append(regionLabel(annotation));
      if (annotation.spatial_annotation_id === state.primary) renderHandles(annotation);
    });
  }
  if (state.drag?.kind === "create") {
    const box = normalizedBox(state.drag.start, state.drag.current);
    const preview = svg("rect", { x: box.x, y: box.y, width: box.width, height: box.height, class: "creation-preview" });
    layer.append(preview);
  }
  updateActionState();
}

function regionRect(annotation, source) {
  const region = annotation.region;
  const rect = svg("rect", {
    x: region.x, y: region.y, width: region.width, height: region.height, rx: 1,
    class: `region ${source} type-${annotation.annotation_type}`,
  });
  const title = svg("title");
  title.textContent = `${humanize(annotation.annotation_type)} · ${annotation.label || shortId(annotation.spatial_annotation_id)}`;
  rect.append(title);
  return rect;
}

function regionLabel(annotation) {
  const label = svg("text", {
    x: annotation.region.x + 3,
    y: Math.max(11, annotation.region.y - 4),
    class: "region-label",
  });
  label.textContent = annotation.label;
  return label;
}

function renderHandles(annotation) {
  const { x, y, width, height } = annotation.region;
  const size = Math.max(4, Math.min(state.canvas.width, state.canvas.height) * 0.012);
  [["nw", x, y], ["ne", x + width, y], ["sw", x, y + height], ["se", x + width, y + height]].forEach(([handle, cx, cy]) => {
    const marker = svg("rect", { x: cx - size / 2, y: cy - size / 2, width: size, height: size, class: "resize-handle" });
    marker.addEventListener("pointerdown", (event) => beginResize(event, annotation, handle));
    layer.append(marker);
  });
}

function beginRegionMove(event, annotation) {
  if (state.mode !== "select" || event.button !== 0 || event.altKey) return;
  event.stopPropagation();
  const id = annotation.spatial_annotation_id;
  if (event.shiftKey) {
    if (state.selected.has(id)) state.selected.delete(id); else state.selected.add(id);
  } else if (!state.selected.has(id)) {
    state.selected = new Set([id]);
  }
  state.primary = id;
  const start = sourcePoint(event);
  state.drag = {
    kind: "move",
    start,
    originals: new Map([...state.selected].map((selectedId) => [selectedId, deepCopy(findRegion(selectedId).region)])),
  };
  layer.setPointerCapture(event.pointerId);
  renderRegions();
  renderInspector();
}

function beginResize(event, annotation, handle) {
  if (event.button !== 0 || event.altKey) return;
  event.stopPropagation();
  state.selected = new Set([annotation.spatial_annotation_id]);
  state.primary = annotation.spatial_annotation_id;
  state.drag = { kind: "resize", handle, start: sourcePoint(event), original: deepCopy(annotation.region) };
  layer.setPointerCapture(event.pointerId);
}

function beginLayerGesture(event) {
  if (state.mode === "pan" || event.button === 1 || event.altKey) {
    event.preventDefault();
    event.stopPropagation();
    state.drag = { kind: "layer-pan", pointer: { x: event.clientX, y: event.clientY } };
    viewer.classList.add("dragging");
    layer.setPointerCapture(event.pointerId);
    return;
  }
  if (event.target !== layer) return;
  if (state.mode === "create" && !sourcePdf.hidden) return;
  if (state.mode === "create") {
    const point = sourcePoint(event);
    state.drag = { kind: "create", start: point, current: point };
    layer.setPointerCapture(event.pointerId);
    renderRegions();
  } else {
    state.selected.clear();
    state.primary = null;
    renderRegions();
    renderInspector();
  }
}

function continueLayerGesture(event) {
  if (!state.drag) return;
  if (state.drag.kind === "layer-pan") {
    event.stopPropagation();
    const transform = currentTransform();
    transform.x += event.clientX - state.drag.pointer.x;
    transform.y += event.clientY - state.drag.pointer.y;
    state.drag.pointer = { x: event.clientX, y: event.clientY };
    applyTransform();
    return;
  }
  const point = clampPoint(sourcePoint(event));
  if (state.drag.kind === "create") {
    state.drag.current = point;
  } else if (state.drag.kind === "move") {
    const dx = point.x - state.drag.start.x;
    const dy = point.y - state.drag.start.y;
    state.drag.originals.forEach((original, id) => {
      const annotation = findRegion(id);
      annotation.region.x = clamp(original.x + dx, 0, original.canvas_width - original.width);
      annotation.region.y = clamp(original.y + dy, 0, original.canvas_height - original.height);
    });
  } else if (state.drag.kind === "resize") {
    resizeRegion(findRegion(state.primary), state.drag.original, state.drag.handle, point);
  }
  renderRegions();
  renderInspector();
}

function endLayerGesture(event) {
  if (!state.drag) return;
  const drag = state.drag;
  state.drag = null;
  if (layer.hasPointerCapture(event.pointerId)) layer.releasePointerCapture(event.pointerId);
  if (drag.kind === "layer-pan") {
    event.stopPropagation();
    viewer.classList.remove("dragging");
    return;
  }
  if (drag.kind === "create") {
    const box = normalizedBox(drag.start, drag.current);
    if (box.width >= 2 && box.height >= 2) createRegion(box);
  } else if (drag.kind === "move" || drag.kind === "resize") {
    scheduleSave();
  }
  renderRegions();
  renderInspector();
}

function createRegion(box) {
  const id = crypto.randomUUID();
  const annotation = {
    schema_version: "1.0",
    spatial_annotation_id: id,
    annotation_type: document.querySelector("#create-type").value,
    state: "present",
    region: {
      schema_version: "1.0", region_id: id,
      source_artifact_id: currentSource().artifact.artifact_id,
      coordinate_space: "source_pixels",
      x: box.x, y: box.y, width: box.width, height: box.height,
      canvas_width: state.canvas.width, canvas_height: state.canvas.height,
      page_number: currentSource().page_number,
    },
    label: null,
  };
  state.draft.spatial_annotations.push(annotation);
  state.selected = new Set([id]);
  state.primary = id;
  setMode("select");
  renderLanes();
  renderRelationshipBuilder();
  scheduleSave();
}

function resizeRegion(annotation, original, handle, point) {
  const min = 2;
  let left = original.x;
  let top = original.y;
  let right = original.x + original.width;
  let bottom = original.y + original.height;
  if (handle.includes("w")) left = clamp(point.x, 0, right - min);
  if (handle.includes("e")) right = clamp(point.x, left + min, original.canvas_width);
  if (handle.includes("n")) top = clamp(point.y, 0, bottom - min);
  if (handle.includes("s")) bottom = clamp(point.y, top + min, original.canvas_height);
  Object.assign(annotation.region, { x: left, y: top, width: right - left, height: bottom - top });
}

function renderInspector() {
  const inspector = document.querySelector("#region-inspector");
  inspector.replaceChildren();
  if (!state.primary) {
    inspector.append(compactMessage(state.selected.size ? `${state.selected.size} regions selected.` : "Select a reviewer region to edit exact source coordinates."));
    return;
  }
  const annotation = findRegion(state.primary);
  if (!annotation) return;
  const grid = document.createElement("div");
  grid.className = "field-grid";
  grid.append(inspectorSelect(annotation, "annotation_type", "Type", REGION_TYPES));
  grid.append(inspectorSelect(annotation, "state", "State", STATES));
  grid.append(inspectorInput(annotation, "label", "Label", "text"));
  ["x", "y", "width", "height"].forEach((field) => grid.append(inspectorInput(annotation.region, field, humanize(field), "number")));
  inspector.append(grid);
}

function inspectorSelect(object, key, labelText, options) {
  const label = document.createElement("label");
  label.append(document.createTextNode(labelText));
  const select = document.createElement("select");
  options.forEach((value) => select.append(new Option(humanize(value), value)));
  select.value = object[key];
  select.addEventListener("change", () => {
    object[key] = select.value;
    renderRegions();
    renderLanes();
    renderRelationshipBuilder();
    scheduleSave();
  });
  label.append(select);
  return label;
}

function inspectorInput(object, key, labelText, type) {
  const label = document.createElement("label");
  label.append(document.createTextNode(labelText));
  const input = document.createElement("input");
  input.type = type;
  input.value = object[key] ?? "";
  if (type === "number") { input.min = "0"; input.step = "any"; }
  input.addEventListener("change", () => {
    if (type === "number") {
      const value = Number(input.value);
      if (["x", "width"].includes(key)) object[key] = clamp(value, key === "width" ? 2 : 0, state.canvas.width - (key === "x" ? object.width : object.x));
      else object[key] = clamp(value, key === "height" ? 2 : 0, state.canvas.height - (key === "y" ? object.height : object.y));
    } else object[key] = optionalText(input.value);
    renderRegions();
    renderLanes();
    scheduleSave();
  });
  label.append(input);
  return label;
}

function deleteSelected() {
  if (!state.selected.size) return;
  state.draft.spatial_annotations = state.draft.spatial_annotations.filter((item) => !state.selected.has(item.spatial_annotation_id));
  state.draft.relationships = state.draft.relationships.filter((item) => !state.selected.has(item.subject_id) && !state.selected.has(item.object_id));
  state.selected.clear();
  state.primary = null;
  renderAll();
  scheduleSave();
}

function splitSelected() {
  if (!state.primary || state.selected.size !== 1) return;
  const original = findRegion(state.primary);
  if (!original || original.region.width < 4) return;
  const first = deepCopy(original);
  const second = deepCopy(original);
  first.spatial_annotation_id = crypto.randomUUID();
  first.region.region_id = first.spatial_annotation_id;
  first.region.width = original.region.width / 2;
  second.spatial_annotation_id = crypto.randomUUID();
  second.region.region_id = second.spatial_annotation_id;
  second.region.x = original.region.x + original.region.width / 2;
  second.region.width = original.region.width / 2;
  if (first.label) first.label += " A";
  if (second.label) second.label += " B";
  state.draft.spatial_annotations = state.draft.spatial_annotations.filter((item) => item.spatial_annotation_id !== original.spatial_annotation_id);
  state.draft.spatial_annotations.push(first, second);
  state.draft.relationships = state.draft.relationships.filter((item) => item.subject_id !== original.spatial_annotation_id && item.object_id !== original.spatial_annotation_id);
  state.draft.relationships.push({ schema_version:"1.0", relationship_id:crypto.randomUUID(), subject_id:first.spatial_annotation_id, relation_type:"grouped_with", object_id:second.spatial_annotation_id });
  state.selected = new Set([first.spatial_annotation_id, second.spatial_annotation_id]);
  state.primary = first.spatial_annotation_id;
  renderAll();
  scheduleSave();
}

function groupSelected() {
  const ids = [...state.selected];
  if (ids.length < 2) return;
  const existing = new Set(state.draft.relationships.filter((item) => item.relation_type === "grouped_with").flatMap((item) => [`${item.subject_id}:${item.object_id}`, `${item.object_id}:${item.subject_id}`]));
  for (let index = 0; index < ids.length - 1; index += 1) {
    const key = `${ids[index]}:${ids[index + 1]}`;
    if (!existing.has(key)) state.draft.relationships.push({ schema_version:"1.0", relationship_id:crypto.randomUUID(), subject_id:ids[index], relation_type:"grouped_with", object_id:ids[index + 1] });
  }
  renderRelationships();
  scheduleSave();
}

function renderLanes() {
  const container = document.querySelector("#lane-list");
  container.replaceChildren();
  const lanes = orderedLanes();
  if (!lanes.length) { container.append(compactMessage("No reviewer lanes.")); return; }
  lanes.forEach((lane, index) => {
    const card = compactCard(lane.label || `Lane ${index + 1}`, shortId(lane.spatial_annotation_id));
    card.classList.add("lane-card");
    const actions = document.createElement("div");
    actions.className = "lane-actions";
    const up = document.createElement("button"); up.type = "button"; up.textContent = "↑"; up.disabled = index === 0; up.addEventListener("click", () => reorderLane(index, -1));
    const down = document.createElement("button"); down.type = "button"; down.textContent = "↓"; down.disabled = index === lanes.length - 1; down.addEventListener("click", () => reorderLane(index, 1));
    actions.append(up, down); card.append(actions); container.append(card);
  });
}

function orderedLanes() {
  const lanes = currentReviewerRegions().filter((item) => item.annotation_type === "lane");
  const byId = new Map(lanes.map((lane) => [lane.spatial_annotation_id, lane]));
  const edges = state.draft.relationships.filter((item) => item.relation_type === "precedes" && byId.has(item.subject_id) && byId.has(item.object_id));
  const next = new Map(edges.map((item) => [item.subject_id, item.object_id]));
  const following = new Set(edges.map((item) => item.object_id));
  const start = lanes.find((lane) => !following.has(lane.spatial_annotation_id));
  const ordered = [];
  const seen = new Set();
  let current = start;
  while (current && !seen.has(current.spatial_annotation_id)) {
    ordered.push(current); seen.add(current.spatial_annotation_id); current = byId.get(next.get(current.spatial_annotation_id));
  }
  ordered.push(...lanes.filter((lane) => !seen.has(lane.spatial_annotation_id)).sort((a, b) => a.region.x - b.region.x));
  return ordered;
}

function reorderLane(index, delta) {
  const lanes = orderedLanes();
  const target = index + delta;
  if (target < 0 || target >= lanes.length) return;
  [lanes[index], lanes[target]] = [lanes[target], lanes[index]];
  const laneIds = new Set(lanes.map((lane) => lane.spatial_annotation_id));
  state.draft.relationships = state.draft.relationships.filter((item) => !(item.relation_type === "precedes" && laneIds.has(item.subject_id) && laneIds.has(item.object_id)));
  for (let position = 0; position < lanes.length - 1; position += 1) {
    state.draft.relationships.push({ schema_version:"1.0", relationship_id:crypto.randomUUID(), subject_id:lanes[position].spatial_annotation_id, relation_type:"precedes", object_id:lanes[position + 1].spatial_annotation_id });
  }
  renderLanes(); renderRelationships(); scheduleSave();
}

function renderRelationshipBuilder() {
  const relationType = document.querySelector("#relationship-type").value;
  const [subjectFilter, objectFilter] = relationshipFilters(relationType);
  const regions = currentReviewerRegions();
  fillRegionSelect(document.querySelector("#relationship-subject"), regions.filter(subjectFilter));
  fillRegionSelect(document.querySelector("#relationship-object"), regions.filter(objectFilter));
}

function relationshipFilters(type) {
  if (type === "labels") return [(item) => item.annotation_type === "label", (item) => item.annotation_type !== "label"];
  if (type === "precedes") return [(item) => item.annotation_type === "lane", (item) => item.annotation_type === "lane"];
  if (type === "target_uses_loading_control") return [(item) => item.annotation_type === "protein_row", (item) => item.annotation_type === "protein_row"];
  if (type === "contains") return [(item) => TYPE_RANK[item.annotation_type] < 4, (item) => TYPE_RANK[item.annotation_type] > 0];
  return [() => true, () => true];
}

function fillRegionSelect(select, regions) {
  const previous = select.value;
  select.replaceChildren();
  regions.forEach((region) => select.append(new Option(regionName(region), region.spatial_annotation_id)));
  if ([...select.options].some((option) => option.value === previous)) select.value = previous;
}

function addRelationship() {
  const type = document.querySelector("#relationship-type").value;
  const subjectId = document.querySelector("#relationship-subject").value;
  const objectId = document.querySelector("#relationship-object").value;
  if (!subjectId || !objectId || subjectId === objectId) { showNotice("Choose two compatible, distinct regions."); return; }
  const duplicate = state.draft.relationships.some((item) => item.subject_id === subjectId && item.relation_type === type && item.object_id === objectId);
  if (duplicate) { showNotice("That spatial relationship already exists."); return; }
  state.draft.relationships.push({ schema_version:"1.0", relationship_id:crypto.randomUUID(), subject_id:subjectId, relation_type:type, object_id:objectId });
  renderRelationships(); scheduleSave();
}

function renderRelationships() {
  const container = document.querySelector("#relationship-list");
  container.replaceChildren();
  if (!state.draft.relationships.length) { container.append(compactMessage("No spatial relationships.")); return; }
  state.draft.relationships.forEach((relationship) => {
    const subject = findRegion(relationship.subject_id);
    const object = findRegion(relationship.object_id);
    const card = document.createElement("div"); card.className = "relationship-card";
    const title = document.createElement("strong"); title.textContent = humanize(relationship.relation_type);
    const detail = document.createElement("span"); detail.textContent = `${subject ? regionName(subject) : shortId(relationship.subject_id)} → ${object ? regionName(object) : shortId(relationship.object_id)}`;
    const remove = document.createElement("button"); remove.type = "button"; remove.textContent = "×"; remove.addEventListener("click", () => { state.draft.relationships = state.draft.relationships.filter((item) => item.relationship_id !== relationship.relationship_id); renderRelationships(); renderLanes(); scheduleSave(); });
    card.append(title, detail, remove); container.append(card);
  });
}

function renderDiff() {
  const summary = document.querySelector("#diff-summary"); const list = document.querySelector("#diff-list");
  summary.replaceChildren(); list.replaceChildren();
  const comparison = state.editor.comparison;
  if (!comparison) { list.append(compactMessage("No spatial prediction selected.")); return; }
  ["modified", "added", "removed", "unchanged"].forEach((status) => { const chip = document.createElement("span"); chip.className = `chip ${status}`; chip.textContent = `${comparison[`${status}_count`]} ${status}`; summary.append(chip); });
  comparison.deltas.filter((delta) => delta.status !== "unchanged").forEach((delta) => {
    const card = compactCard(shortId(delta.spatial_annotation_id), delta.changed_fields.join(", ") || humanize(delta.status));
    card.classList.add("diff-card", delta.status); list.append(card);
  });
  if (!list.children.length) list.append(compactMessage("Reviewer geometry matches prediction."));
}

function renderErrorCodes() {
  const container = document.querySelector("#error-codes"); container.replaceChildren();
  const selected = new Set(state.editor.head_revision?.error_codes || []);
  state.editor.error_codes.forEach((record) => {
    const label = document.createElement("label"); const input = document.createElement("input"); input.type = "checkbox"; input.value = record.code; input.checked = selected.has(record.code); input.addEventListener("change", () => scheduleSave()); label.append(input, document.createTextNode(humanize(record.code))); container.append(label);
  });
}

function selectedErrorCodes() { return [...document.querySelectorAll("#error-codes input:checked")].map((input) => input.value); }

function renderRevisions() {
  const container = document.querySelector("#revision-list"); container.replaceChildren();
  if (!state.editor.revisions.length) { container.append(compactMessage("No committed revisions.")); return; }
  [...state.editor.revisions].reverse().forEach((revision) => {
    const card = compactCard(`Revision ${revision.revision_number}`, `${revision.spatial_annotation_count} regions · ${formatDate(revision.created_at)}`); card.classList.add("revision-card");
    if (revision.revision_id !== state.headRevisionId) { const restore = document.createElement("button"); restore.type = "button"; restore.textContent = "Restore"; restore.addEventListener("click", () => undoRevision(revision)); card.append(restore); }
    container.append(card);
  });
}

async function acceptPrediction(regionIds, acceptAll) {
  if (!state.selectedPredictionId) return;
  try {
    const response = await fetch(`/api/v1/evaluation-cases/${caseId}/spatial-annotation-drafts/accept-prediction`, { method:"POST", headers:{"Content-Type":"application/json"}, body:JSON.stringify({ schema_version:"1.0", prediction_id:state.selectedPredictionId, base_set:state.draft, accepted_region_ids:regionIds, accept_all:acceptAll }) });
    if (!response.ok) throw new Error(await responseMessage(response));
    const payload = await response.json(); state.draft = payload.annotation_set; state.editor.comparison = payload.comparison; renderAll(); scheduleSave(0);
  } catch (error) { showNotice(error.message || "Predicted regions could not be accepted."); }
}

async function changePrediction() {
  if (state.mutationVersion !== state.savedVersion) await saveNow();
  if (state.mutationVersion !== state.savedVersion) { predictionSelect.value = state.selectedPredictionId || ""; return; }
  await loadEditor(predictionSelect.value || null);
}

function scheduleSave(delay = 800) { state.mutationVersion += 1; clearTimeout(state.saveTimer); state.saveTimer = setTimeout(saveNow, delay); setSaveStatus("Unsaved geometry"); }

async function saveNow() {
  clearTimeout(state.saveTimer);
  if (state.saving || !state.draft || state.mutationVersion === state.savedVersion) return;
  state.saving = true; const version = state.mutationVersion; const snapshot = deepCopy(state.draft); let saved = false; setSaveStatus("Saving revision…"); hideNotice();
  try {
    const response = await fetch(`/api/v1/evaluation-cases/${caseId}/spatial-annotations`, { method:"PUT", headers:{"Content-Type":"application/json"}, body:JSON.stringify({ schema_version:"1.0", reviewer_id:reviewerId, expected_head_revision_id:state.headRevisionId, annotation_set:snapshot, rationale:optionalText(rationale.value), error_codes:selectedErrorCodes() }) });
    if (!response.ok) throw new Error(await responseMessage(response));
    const payload = await response.json(); state.documentId = payload.annotation.annotation_id; state.headRevisionId = payload.revision.revision_id; state.savedVersion = version; saved = true; setSaveStatus(`Saved revision ${payload.revision.revision_number}`); if (state.mutationVersion === version) await refreshMetadata();
  } catch (error) { showNotice(error.message || "Spatial save failed; the browser draft remains available."); setSaveStatus("Save failed", true); }
  finally { state.saving = false; if (saved && state.mutationVersion !== state.savedVersion) state.saveTimer = setTimeout(saveNow, 800); }
}

async function refreshMetadata() {
  const params = new URLSearchParams({ reviewer_id:reviewerId }); if (state.selectedPredictionId) params.set("prediction_id", state.selectedPredictionId);
  const response = await fetch(`/api/v1/evaluation-cases/${caseId}/spatial-editor?${params}`); if (!response.ok) return; const refreshed = await response.json(); if (state.mutationVersion !== state.savedVersion) return;
  state.editor = refreshed; state.draft = deepCopy(refreshed.draft_set); state.documentId = refreshed.annotation_document?.annotation_id || null; state.headRevisionId = refreshed.head_revision?.revision_id || null;
  state.selected = new Set([...state.selected].filter((id) => findRegion(id)));
  if (state.primary && !findRegion(state.primary)) state.primary = null;
  renderAll();
}

async function undoRevision(revision) {
  if (!state.documentId || !state.headRevisionId || !confirm(`Restore spatial revision ${revision.revision_number} as a new revision?`)) return;
  try {
    const response = await fetch(`/api/v1/annotations/${state.documentId}/spatial-undo`, { method:"POST", headers:{"Content-Type":"application/json"}, body:JSON.stringify({ schema_version:"1.0", reviewer_id:reviewerId, expected_head_revision_id:state.headRevisionId, target_revision_id:revision.revision_id, rationale:`Restored spatial revision ${revision.revision_number}` }) });
    if (!response.ok) throw new Error(await responseMessage(response)); state.mutationVersion += 1; state.savedVersion = state.mutationVersion; await loadEditor(state.selectedPredictionId);
  } catch (error) { showNotice(error.message || "Spatial revision restore failed."); }
}

function handleShortcut(event) {
  if (event.metaKey || event.ctrlKey) { if (event.key.toLowerCase() === "s") { event.preventDefault(); saveNow(); } return; }
  if (["INPUT", "SELECT", "TEXTAREA"].includes(document.activeElement?.tagName)) return;
  if (event.key.toLowerCase() === "v") setMode("select");
  else if (event.key.toLowerCase() === "n") setMode("create");
  else if (event.key.toLowerCase() === "p") setMode("pan");
  else if (event.key === "Delete" || event.key === "Backspace") { event.preventDefault(); deleteSelected(); }
  else if (["ArrowLeft", "ArrowRight", "ArrowUp", "ArrowDown"].includes(event.key)) { event.preventDefault(); nudgeSelected(event.key, event.shiftKey ? 10 : 1); }
  else if (event.key === "[") reorderPrimaryLane(-1);
  else if (event.key === "]") reorderPrimaryLane(1);
  else if (event.key === "Escape") { state.selected.clear(); state.primary = null; renderRegions(); renderInspector(); }
}

function nudgeSelected(key, amount) {
  if (!state.selected.size) return; const delta = { ArrowLeft:[-amount,0], ArrowRight:[amount,0], ArrowUp:[0,-amount], ArrowDown:[0,amount] }[key];
  state.selected.forEach((id) => { const region = findRegion(id)?.region; if (!region) return; region.x = clamp(region.x + delta[0], 0, region.canvas_width - region.width); region.y = clamp(region.y + delta[1], 0, region.canvas_height - region.height); }); renderRegions(); renderInspector(); scheduleSave();
}

function reorderPrimaryLane(delta) { const lanes = orderedLanes(); const index = lanes.findIndex((lane) => lane.spatial_annotation_id === state.primary); if (index >= 0) reorderLane(index, delta); }
function setMode(mode) { state.mode = mode; document.querySelectorAll(".mode").forEach((button) => button.classList.toggle("active", button.dataset.mode === mode)); viewer.classList.toggle("creating", mode === "create"); viewer.classList.toggle("panning", mode === "pan"); }

function beginPan(event) { if (event.target !== viewer || event.button !== 0) return; const transform = currentTransform(); if (!transform) return; state.drag = { kind:"pan", pointer:{x:event.clientX,y:event.clientY} }; viewer.classList.add("dragging"); viewer.setPointerCapture(event.pointerId); }
function continuePan(event) { if (state.drag?.kind !== "pan") return; const transform = currentTransform(); transform.x += event.clientX - state.drag.pointer.x; transform.y += event.clientY - state.drag.pointer.y; state.drag.pointer = {x:event.clientX,y:event.clientY}; applyTransform(); }
function endPan(event) { if (state.drag?.kind !== "pan") return; state.drag = null; viewer.classList.remove("dragging"); if (viewer.hasPointerCapture(event.pointerId)) viewer.releasePointerCapture(event.pointerId); }
function onWheel(event) { event.preventDefault(); zoomBy(event.deltaY < 0 ? 1.1 : 1 / 1.1); }
function zoomBy(factor) { const transform = currentTransform(); if (!transform) return; transform.zoom = clamp(transform.zoom * factor, .2, 8); applyTransform(); }
function rotateBy(degrees) { const transform = currentTransform(); if (!transform) return; transform.rotation = (transform.rotation + degrees) % 360; applyTransform(); }
function resetView() { const transform = currentTransform(); if (!transform) return; Object.assign(transform, {zoom:1,x:0,y:0,rotation:0}); applyTransform(); }
function restoreTransform(sourceId) { if (!state.transforms.has(sourceId)) state.transforms.set(sourceId, {zoom:1,x:0,y:0,rotation:0}); applyTransform(); }
function saveTransform() { if (currentSource()) state.transforms.set(currentSource().artifact.artifact_id, {...currentTransform()}); }
function currentTransform() { return currentSource() ? state.transforms.get(currentSource().artifact.artifact_id) : null; }
function applyTransform() { const transform = currentTransform(); if (!transform) return; canvasTransform.style.transform = `translate(-50%, -50%) translate(${transform.x}px, ${transform.y}px) scale(${transform.zoom}) rotate(${transform.rotation}deg)`; document.querySelector("#zoom-label").textContent = `${Math.round(transform.zoom * 100)}%`; }
async function toggleFullscreen() { if (document.fullscreenElement) await document.exitFullscreen(); else await document.querySelector(".viewer-shell").requestFullscreen(); }

function sourcePoint(event) { const point = layer.createSVGPoint(); point.x = event.clientX; point.y = event.clientY; const matrix = layer.getScreenCTM(); return matrix ? point.matrixTransform(matrix.inverse()) : {x:0,y:0}; }
function clampPoint(point) { return {x:clamp(point.x,0,state.canvas.width), y:clamp(point.y,0,state.canvas.height)}; }
function updateCoordinateStatus(event) { const point = clampPoint(sourcePoint(event)); document.querySelector("#coordinate-status").textContent = `x ${point.x.toFixed(1)} · y ${point.y.toFixed(1)} · ${state.canvas.width} × ${state.canvas.height} px`; }
function normalizedBox(first, second) { const x = clamp(Math.min(first.x,second.x),0,state.canvas.width); const y = clamp(Math.min(first.y,second.y),0,state.canvas.height); return {x,y,width:clamp(Math.abs(second.x-first.x),0,state.canvas.width-x),height:clamp(Math.abs(second.y-first.y),0,state.canvas.height-y)}; }

function reviewerRegions() { return state.draft?.spatial_annotations || []; }
function currentReviewerRegions() {
  const source = currentSource();
  if (!source) return [];
  return reviewerRegions().filter((item) => regionMatchesSource(item, source));
}
function predictionRegions() { return state.editor?.prediction_set?.spatial_annotations || []; }
function regionMatchesSource(annotation, source) { return source != null && annotation.region.source_artifact_id === source.artifact.artifact_id && (source.page_number == null || annotation.region.page_number === source.page_number); }
function findRegion(id) { return reviewerRegions().find((item) => item.spatial_annotation_id === id); }
function currentSource() { return state.workbench?.sources[state.sourceIndex] || null; }
function regionName(annotation) { return annotation.label || `${humanize(annotation.annotation_type)} ${shortId(annotation.spatial_annotation_id)}`; }
function updateActionState() { document.querySelector("#delete-region").disabled = !state.selected.size; document.querySelector("#split-region").disabled = state.selected.size !== 1; document.querySelector("#group-regions").disabled = state.selected.size < 2; }
function compactCard(titleText, detailText) { const card = document.createElement("div"); card.className = "compact-card"; const title = document.createElement("strong"); title.textContent = titleText; const detail = document.createElement("span"); detail.textContent = detailText; card.append(title,detail); return card; }
function compactMessage(message) { const row = document.createElement("p"); row.className = "help"; row.textContent = message; return row; }
function svg(name, attributes = {}) { const element = document.createElementNS("http://www.w3.org/2000/svg", name); Object.entries(attributes).forEach(([key,value]) => element.setAttribute(key,value)); return element; }
function clamp(value,min,max) { return Math.max(min,Math.min(max,value)); }
function deepCopy(value) { return JSON.parse(JSON.stringify(value)); }
function optionalText(value) { const result = String(value ?? "").trim(); return result || null; }
function shortId(value) { return String(value).slice(0,8); }
function humanize(value) { return String(value).replaceAll("_"," ").replace(/\b\w/g,(letter)=>letter.toUpperCase()); }
function formatDate(value) { return new Intl.DateTimeFormat(undefined,{dateStyle:"medium",timeStyle:"short"}).format(new Date(value)); }
function showNotice(message) { notice.textContent = message; notice.hidden = false; }
function hideNotice() { notice.hidden = true; notice.textContent = ""; }
function setSaveStatus(message,error=false) { saveStatus.textContent = message; saveStatus.classList.toggle("error",error); }
function localReviewerId() { const key="hiveblot.localReviewerId"; let value=localStorage.getItem(key); if (!value) { value=crypto.randomUUID(); localStorage.setItem(key,value); } return value; }
async function responseMessage(response) { try { const payload=await response.json(); if (typeof payload.detail === "string") return payload.detail; if (Array.isArray(payload.detail)) return payload.detail.map((item)=>`${item.loc?.join(".")}: ${item.msg}`).join("\n"); return `Request failed (${response.status}).`; } catch (_) { return `Request failed (${response.status}).`; } }
