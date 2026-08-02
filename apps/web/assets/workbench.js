"use strict";

const caseId = window.location.pathname.split("/").filter(Boolean).at(-1);
const sourceList = document.querySelector("#source-list");
const sourceTemplate = document.querySelector("#source-template");
const viewer = document.querySelector("#viewer");
const viewerMessage = document.querySelector("#viewer-message");
const mediaTransform = document.querySelector("#media-transform");
const sourceImage = document.querySelector("#source-image");
const sourcePdf = document.querySelector("#source-pdf");
const overlayLayer = document.querySelector("#overlay-layer");
const loadStatus = document.querySelector("#load-status");

const state = {
  workbench: null,
  pipelineRuns: [],
  sourceIndex: -1,
  selectedField: null,
  transforms: new Map(),
  visibleOverlays: new Set(["prediction", "reviewer", "adjudication"]),
  dragging: false,
  pointer: null,
};

document.querySelector("#case-id").textContent = caseId;
document.querySelector("#annotation-link").href = `/annotate/${caseId}`;
document.querySelector("#spatial-link").href = `/spatial/${caseId}`;
document.querySelectorAll("input[name=overlay]").forEach((input) => input.addEventListener("change", toggleOverlay));
document.querySelectorAll(".tab").forEach((tab) => tab.addEventListener("click", selectTab));
document.querySelector("#zoom-in").addEventListener("click", () => zoomBy(1.2));
document.querySelector("#zoom-out").addEventListener("click", () => zoomBy(1 / 1.2));
document.querySelector("#rotate-left").addEventListener("click", () => rotateBy(-90));
document.querySelector("#rotate-right").addEventListener("click", () => rotateBy(90));
document.querySelector("#reset-view").addEventListener("click", resetView);
document.querySelector("#fullscreen").addEventListener("click", toggleFullscreen);
document.querySelector("#previous-page").addEventListener("click", () => pageBy(-1));
document.querySelector("#next-page").addEventListener("click", () => pageBy(1));
document.querySelector("#clear-field").addEventListener("click", () => selectField(null));
viewer.addEventListener("wheel", onWheel, { passive: false });
viewer.addEventListener("pointerdown", beginPan);
viewer.addEventListener("pointermove", continuePan);
viewer.addEventListener("pointerup", endPan);
viewer.addEventListener("pointercancel", endPan);
viewer.addEventListener("dblclick", resetView);
window.addEventListener("resize", applyTransform);

loadWorkbench();
loadPipelineHistory();

async function loadWorkbench() {
  sourceList.setAttribute("aria-busy", "true");
  try {
    const response = await fetch(`/api/v1/evaluation-cases/${caseId}/workbench`);
    if (!response.ok) throw new Error(await responseMessage(response));
    state.workbench = await response.json();
    renderSources();
    renderSupplementary();
    renderFields();
    if (state.workbench.sources.length) selectSource(0);
    else showViewerMessage("This case has no source artifacts.", true);
  } catch (error) {
    showViewerMessage(error.message || "Source evidence could not be loaded.", true);
  } finally {
    sourceList.setAttribute("aria-busy", "false");
  }
}

async function loadPipelineHistory() {
  const container = document.querySelector("#pipeline-run-list");
  container.setAttribute("aria-busy", "true");
  try {
    const response = await fetch(`/api/v1/evaluation-cases/${caseId}/pipeline-runs`);
    if (!response.ok) throw new Error(await responseMessage(response));
    const payload = await response.json();
    state.pipelineRuns = payload.runs;
    renderPipelineHistory();
  } catch (error) {
    container.textContent = error.message || "Invocation history could not be loaded.";
    container.classList.add("error");
  } finally {
    container.setAttribute("aria-busy", "false");
  }
}

function renderPipelineHistory() {
  const container = document.querySelector("#pipeline-run-list");
  container.replaceChildren();
  container.classList.remove("error");
  if (!state.pipelineRuns.length) {
    container.textContent = "No pipeline runs have been recorded for this case.";
    return;
  }
  [...state.pipelineRuns].reverse().forEach((detail) => {
    const run = document.createElement("article");
    run.className = "pipeline-run-card";
    const heading = document.createElement("div");
    heading.className = "pipeline-run-heading";
    const title = document.createElement("strong");
    title.textContent = `${detail.run.pipeline_name} · ${detail.run.pipeline_version}`;
    heading.append(title, statusBadge(detail.run.status));
    const meta = document.createElement("p");
    meta.className = "pipeline-meta";
    meta.textContent = `${formatDate(detail.run.created_at)} · trace ${shortId(detail.run.trace_id)}`;
    run.append(heading, meta);
    detail.invocations.forEach((invocation) => run.append(invocationCard(invocation)));
    if (detail.publications.length) {
      const published = document.createElement("p");
      published.className = "pipeline-publication";
      published.textContent = `${detail.publications.length} published snapshot${detail.publications.length === 1 ? "" : "s"}`;
      run.append(published);
    }
    container.append(run);
  });
}

function invocationCard(invocation) {
  const card = document.createElement("div");
  card.className = "invocation-card";
  const heading = document.createElement("div");
  heading.className = "invocation-heading";
  const title = document.createElement("strong");
  title.textContent = invocation.component.component_key;
  heading.append(title, statusBadge(invocation.status));
  const producer = document.createElement("p");
  producer.className = "pipeline-meta";
  producer.textContent = `${humanize(invocation.component.component_type)} · ${invocation.component.producer_name}@${invocation.component.producer_version}`;
  card.append(heading, producer);
  if (invocation.replay_of_invocation_id) {
    const replay = document.createElement("p");
    replay.className = "replay-label";
    replay.textContent = `Replay of ${shortId(invocation.replay_of_invocation_id)}`;
    card.append(replay);
  }
  if (invocation.result) {
    const metrics = document.createElement("p");
    metrics.className = "pipeline-meta";
    metrics.textContent = `${invocation.result.latency_ms} ms · $${(invocation.result.cost_microusd / 1000000).toFixed(6)} · ${invocation.result.validation_issues.length} warnings`;
    card.append(metrics);
    if (invocation.result.error_message) {
      const error = document.createElement("p");
      error.className = "invocation-error";
      error.textContent = invocation.result.error_message;
      card.append(error);
    }
    if (invocation.result.raw_output_json) card.append(outputDetails("Raw output", invocation.result.raw_output_json));
    if (invocation.result.normalized_output_json) card.append(outputDetails("Normalized output", invocation.result.normalized_output_json));
  }
  return card;
}

function statusBadge(status) {
  const badge = document.createElement("span");
  badge.className = `status-badge status-${status}`;
  badge.textContent = humanize(status);
  return badge;
}

function outputDetails(label, value) {
  const details = document.createElement("details");
  const summary = document.createElement("summary");
  summary.textContent = label;
  const pre = document.createElement("pre");
  pre.textContent = prettyJson(value);
  details.append(summary, pre);
  return details;
}

function prettyJson(value) {
  try { return JSON.stringify(JSON.parse(value), null, 2); }
  catch (_) { return value; }
}

function shortId(value) { return value.slice(0, 8); }

function renderSources() {
  sourceList.replaceChildren();
  state.workbench.sources.forEach((source, index) => {
    const card = sourceTemplate.content.firstElementChild.cloneNode(true);
    card.querySelector(".source-role").textContent = humanize(source.artifact_role);
    card.querySelector(".source-name").textContent = source.artifact.original_filename;
    card.querySelector(".source-meta").textContent = [source.artifact.media_type, formatBytes(source.artifact.byte_size), source.page_number ? `page ${source.page_number}` : null].filter(Boolean).join(" · ");
    card.querySelector(".source-hash").textContent = `sha256:${source.artifact.sha256}`;
    card.addEventListener("click", () => selectSource(index));
    sourceList.append(card);
  });
}

function renderSupplementary() {
  const container = document.querySelector("#supplementary-list");
  container.replaceChildren();
  const supplements = state.workbench.sources.filter((source) => source.artifact_role === "supplementary");
  if (!supplements.length) {
    container.textContent = "No supplementary artifacts attached.";
    return;
  }
  supplements.forEach((source) => {
    const button = document.createElement("button");
    button.className = "field-button";
    button.textContent = source.artifact.original_filename;
    button.addEventListener("click", () => selectSource(state.workbench.sources.indexOf(source)));
    container.append(button);
  });
}

async function selectSource(index) {
  saveCurrentTransform();
  state.sourceIndex = index;
  state.selectedField = null;
  document.querySelector("#clear-field").hidden = true;
  document.querySelectorAll(".source-card").forEach((card, position) => card.classList.toggle("active", position === index));
  const source = currentSource();
  document.querySelector("#viewer-heading").textContent = source.artifact.original_filename;
  renderDetails(source);
  restoreTransform(source.artifact.artifact_id, source.page_number);
  await loadSource(source);
}

async function loadSource(source) {
  showViewerMessage("Requesting expiring source access…");
  loadStatus.textContent = "Loading directly from object storage";
  sourceImage.removeAttribute("src");
  sourcePdf.removeAttribute("src");
  sourceImage.hidden = true;
  sourcePdf.hidden = true;
  overlayLayer.replaceChildren();
  try {
    const response = await fetch(`/api/v1/artifacts/${source.artifact.artifact_id}/download-url`, { method: "POST" });
    if (!response.ok) throw new Error(await responseMessage(response));
    const access = await response.json();
    if (source.artifact.media_type === "application/pdf") {
      sourcePdf.hidden = false;
      sourcePdf.src = pdfUrl(access.url, currentTransform().page);
      revealMedia();
      renderOverlays();
    } else if (source.artifact.media_type.startsWith("image/")) {
      sourceImage.hidden = false;
      sourceImage.onload = () => {
        revealMedia();
        renderOverlays();
      };
      sourceImage.onerror = () => {
        showViewerMessage("The signed image could not be decoded.", true);
        loadStatus.textContent = "Source unavailable";
      };
      sourceImage.src = access.url;
    } else {
      revealMedia();
      showViewerMessage(`Preview is unavailable for ${source.artifact.media_type}. Use the signed object endpoint to download it.`, true);
      loadStatus.textContent = "Preview unavailable";
    }
  } catch (error) {
    showViewerMessage(error.message || "The source artifact is unavailable.", true);
    loadStatus.textContent = "Source unavailable";
  }
}

function revealMedia() {
  viewerMessage.hidden = true;
  mediaTransform.hidden = false;
  loadStatus.textContent = "Streaming from signed object URL";
  applyTransform();
}

function showViewerMessage(message, error = false) {
  mediaTransform.hidden = true;
  viewerMessage.hidden = false;
  viewerMessage.textContent = message;
  viewerMessage.classList.toggle("error", error);
}

function renderOverlays() {
  overlayLayer.replaceChildren();
  const source = currentSource();
  const overlays = state.workbench.overlays.filter((overlay) => overlay.source_artifact_id === source.artifact.artifact_id && state.visibleOverlays.has(overlay.overlay_source));
  const reference = overlays[0]?.region;
  if (!reference || source.artifact.media_type === "application/pdf") {
    overlayLayer.removeAttribute("viewBox");
    return;
  }
  overlayLayer.setAttribute("viewBox", `0 0 ${reference.canvas_width} ${reference.canvas_height}`);
  overlays.forEach((overlay) => {
    const rect = document.createElementNS("http://www.w3.org/2000/svg", "rect");
    rect.setAttribute("x", overlay.region.x);
    rect.setAttribute("y", overlay.region.y);
    rect.setAttribute("width", overlay.region.width);
    rect.setAttribute("height", overlay.region.height);
    rect.setAttribute("rx", "2");
    rect.classList.add("overlay-region", overlay.overlay_source);
    const linked = overlay.linked_field_keys.includes(state.selectedField);
    if (state.selectedField && !linked) rect.classList.add("dimmed");
    if (state.selectedField && linked) rect.classList.add("focused");
    rect.addEventListener("click", (event) => {
      event.stopPropagation();
      if (overlay.linked_field_keys.length) selectField(overlay.linked_field_keys[0]);
    });
    const title = document.createElementNS("http://www.w3.org/2000/svg", "title");
    title.textContent = `${humanize(overlay.overlay_source)}${overlay.label ? `: ${overlay.label}` : ""}`;
    rect.append(title);
    overlayLayer.append(rect);
    if (overlay.label) {
      const label = document.createElementNS("http://www.w3.org/2000/svg", "text");
      label.setAttribute("x", overlay.region.x + 4);
      label.setAttribute("y", Math.max(14, overlay.region.y - 5));
      label.classList.add("overlay-label");
      label.textContent = overlay.label;
      overlayLayer.append(label);
    }
  });
}

function renderFields() {
  const fieldList = document.querySelector("#field-list");
  fieldList.replaceChildren();
  const fields = [...new Set(state.workbench.overlays.flatMap((overlay) => overlay.linked_field_keys))].sort();
  if (!fields.length) {
    fieldList.textContent = "No source-linked fields are available.";
    return;
  }
  fields.forEach((field) => {
    const button = document.createElement("button");
    button.className = "field-button";
    button.dataset.field = field;
    button.textContent = field;
    button.addEventListener("click", () => selectField(field));
    fieldList.append(button);
  });
}

function selectField(field) {
  state.selectedField = field;
  document.querySelector("#clear-field").hidden = !field;
  document.querySelectorAll(".field-button[data-field]").forEach((button) => button.classList.toggle("active", button.dataset.field === field));
  if (field) {
    const overlay = state.workbench.overlays.find((item) => item.linked_field_keys.includes(field));
    const sourceIndex = overlay ? state.workbench.sources.findIndex((source) => source.artifact.artifact_id === overlay.source_artifact_id) : -1;
    if (sourceIndex >= 0 && sourceIndex !== state.sourceIndex) {
      selectSource(sourceIndex).then(() => {
        state.selectedField = field;
        renderOverlays();
      });
      return;
    }
  }
  renderOverlays();
}

function renderDetails(source) {
  setText("#caption", source.caption, "No caption recorded.");
  setText("#nearby-text", source.nearby_text, "No nearby text recorded.");
  const provenance = document.querySelector("#provenance");
  provenance.replaceChildren();
  [
    ["Artifact ID", source.artifact.artifact_id],
    ["SHA-256", source.artifact.sha256],
    ["Media type", source.artifact.media_type],
    ["Byte size", source.artifact.byte_size.toLocaleString()],
    ["Source URI", source.artifact.source_uri || "Not recorded"],
    ["Acquisition", humanize(source.artifact.acquisition_method)],
    ["Visibility", humanize(source.artifact.visibility)],
    ["Created", formatDate(source.artifact.created_at)],
  ].forEach(([term, value]) => {
    const dt = document.createElement("dt");
    const dd = document.createElement("dd");
    dt.textContent = term;
    dd.textContent = value;
    provenance.append(dt, dd);
  });
  const relationships = document.querySelector("#relationship-list");
  relationships.replaceChildren();
  if (!source.artifact.relationships.length) relationships.textContent = "No artifact relationships recorded.";
  source.artifact.relationships.forEach((relationship) => {
    const row = document.createElement("div");
    row.textContent = `${humanize(relationship.kind)} → ${relationship.related_artifact_id}`;
    relationships.append(row);
  });
}

function toggleOverlay(event) {
  if (event.target.checked) state.visibleOverlays.add(event.target.value);
  else state.visibleOverlays.delete(event.target.value);
  renderOverlays();
}

function currentSource() { return state.workbench?.sources[state.sourceIndex] || null; }
function currentTransform() {
  const source = currentSource();
  return source ? state.transforms.get(source.artifact.artifact_id) : null;
}

function restoreTransform(artifactId, pageNumber) {
  if (!state.transforms.has(artifactId)) state.transforms.set(artifactId, { zoom: 1, x: 0, y: 0, rotation: 0, page: pageNumber || 1 });
  applyTransform();
}

function saveCurrentTransform() {
  if (state.sourceIndex < 0) return;
  state.transforms.set(currentSource().artifact.artifact_id, { ...currentTransform() });
}

function applyTransform() {
  if (state.sourceIndex < 0) return;
  const transform = currentTransform();
  if (!transform) return;
  mediaTransform.style.transform = `translate(-50%, -50%) translate(${transform.x}px, ${transform.y}px) scale(${transform.zoom}) rotate(${transform.rotation}deg)`;
  document.querySelector("#zoom-label").textContent = `${Math.round(transform.zoom * 100)}%`;
  document.querySelector("#page-label").textContent = `Page ${transform.page}`;
}

function zoomBy(factor) {
  const transform = currentTransform();
  if (!transform) return;
  transform.zoom = Math.max(.2, Math.min(8, transform.zoom * factor));
  applyTransform();
}

function rotateBy(degrees) {
  const transform = currentTransform();
  if (!transform) return;
  transform.rotation = (transform.rotation + degrees) % 360;
  applyTransform();
}

function resetView() {
  const transform = currentTransform();
  if (!transform) return;
  Object.assign(transform, { zoom: 1, x: 0, y: 0, rotation: 0 });
  applyTransform();
}

function pageBy(delta) {
  const source = currentSource();
  const transform = currentTransform();
  if (!source || !transform) return;
  transform.page = Math.max(1, transform.page + delta);
  if (source.artifact.media_type === "application/pdf" && sourcePdf.src) sourcePdf.src = pdfUrl(sourcePdf.src.split("#")[0], transform.page);
  applyTransform();
}

function onWheel(event) {
  event.preventDefault();
  zoomBy(event.deltaY < 0 ? 1.1 : 1 / 1.1);
}

function beginPan(event) {
  if (event.button !== 0) return;
  state.dragging = true;
  state.pointer = { x: event.clientX, y: event.clientY };
  viewer.classList.add("dragging");
  viewer.setPointerCapture(event.pointerId);
}

function continuePan(event) {
  if (!state.dragging) return;
  const transform = currentTransform();
  if (!transform) return;
  transform.x += event.clientX - state.pointer.x;
  transform.y += event.clientY - state.pointer.y;
  state.pointer = { x: event.clientX, y: event.clientY };
  applyTransform();
}

function endPan(event) {
  if (!state.dragging) return;
  state.dragging = false;
  state.pointer = null;
  viewer.classList.remove("dragging");
  if (viewer.hasPointerCapture(event.pointerId)) viewer.releasePointerCapture(event.pointerId);
}

async function toggleFullscreen() {
  if (document.fullscreenElement) await document.exitFullscreen();
  else await document.querySelector(".viewer-shell").requestFullscreen();
}

function selectTab(event) {
  const target = event.currentTarget.dataset.tab;
  document.querySelectorAll(".tab").forEach((tab) => {
    const active = tab.dataset.tab === target;
    tab.classList.toggle("active", active);
    tab.setAttribute("aria-selected", String(active));
  });
  document.querySelectorAll(".tab-panel").forEach((panel) => panel.classList.toggle("active", panel.id === `tab-${target}`));
}

function pdfUrl(url, page) { return `${url}#page=${page}&zoom=page-width`; }
function humanize(value) { return value.replaceAll("_", " ").replace(/\b\w/g, (letter) => letter.toUpperCase()); }
function formatBytes(value) { if (value < 1024) return `${value} B`; if (value < 1048576) return `${(value / 1024).toFixed(1)} KB`; return `${(value / 1048576).toFixed(1)} MB`; }
function formatDate(value) { return new Intl.DateTimeFormat(undefined, { dateStyle: "medium", timeStyle: "short" }).format(new Date(value)); }
function setText(selector, value, fallback) { const element = document.querySelector(selector); element.textContent = value || fallback; element.classList.toggle("muted", !value); }
async function responseMessage(response) { try { const payload = await response.json(); return typeof payload.detail === "string" ? payload.detail : `Request failed (${response.status}).`; } catch (_) { return `Request failed (${response.status}).`; } }
