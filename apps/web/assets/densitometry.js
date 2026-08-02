"use strict";

const caseId = window.location.pathname.split("/").filter(Boolean).at(-1);
const geometrySelect = document.querySelector("#geometry-select");
const sourceImage = document.querySelector("#source-image");
const geometryLayer = document.querySelector("#geometry-layer");
const visual = document.querySelector("#visual");
const viewerMessage = document.querySelector("#viewer-message");
const notice = document.querySelector("#notice");
const runButton = document.querySelector("#run-analysis");
const state = { workbench:null, optionIndex:0, selectedAttempt:null, view:"source", running:false };

document.querySelector("#case-id").textContent = caseId;
document.querySelector("#evidence-link").href = `/workbench/${caseId}`;
document.querySelector("#spatial-link").href = `/spatial/${caseId}`;
document.querySelector("#adjust-geometry").href = `/spatial/${caseId}`;
geometrySelect.addEventListener("change", () => { state.optionIndex = Number(geometrySelect.value); renderOption(); });
document.querySelector("#normalization-method").addEventListener("change", updateControlState);
document.querySelector("#background-method").addEventListener("change", updateControlState);
document.querySelector("#show-source").addEventListener("click", () => setView("source"));
document.querySelector("#show-overlay").addEventListener("click", () => setView("overlay"));
runButton.addEventListener("click", runAnalysis);
loadWorkbench();

async function loadWorkbench(preferredInvocation = null) {
  setStatus("Loading…"); hideNotice();
  try {
    const response = await fetch(`/api/v1/evaluation-cases/${caseId}/densitometry-workbench`);
    if (!response.ok) throw new Error(await responseMessage(response));
    state.workbench = await response.json();
    renderGeometryOptions();
    renderOption();
    renderAttempts();
    const preferred = state.workbench.attempts.find((item) => item.invocation_id === preferredInvocation);
    if (preferred) selectAttempt(preferred);
    else if (state.workbench.attempts.length) selectAttempt(state.workbench.attempts[0]);
    else renderResult(null);
    setStatus(`${state.workbench.geometry_options.length} geometry option${state.workbench.geometry_options.length === 1 ? "" : "s"}`);
  } catch (error) {
    showNotice(error.message || "The densitometry workbench could not be loaded.");
    setStatus("Load failed");
  }
}

function renderGeometryOptions() {
  geometrySelect.replaceChildren();
  state.workbench.geometry_options.forEach((option, index) => {
    const label = `${option.label} · ${humanize(option.image_kind)} · ${shortId(option.image_artifact.artifact_id)}`;
    geometrySelect.append(new Option(label, String(index), false, index === state.optionIndex));
  });
  geometrySelect.disabled = !state.workbench.geometry_options.length;
  runButton.disabled = !state.workbench.geometry_options.length;
}

function renderOption() {
  const option = currentOption();
  const meta = document.querySelector("#geometry-meta");
  if (!option) {
    meta.textContent = "No complete geometry is available. Create lanes, protein rows, and one band per lane/target pair in the spatial editor.";
    visual.hidden = true; viewerMessage.hidden = false; viewerMessage.textContent = "Complete geometry is required.";
    document.querySelector("#loading-control").replaceChildren(new Option("No target", ""));
    return;
  }
  meta.textContent = `${option.lanes.length} lanes · ${option.targets.length} targets · ${option.bands.length} bands · ${formatDate(option.created_at)}`;
  document.querySelector("#image-title").textContent = `${humanize(option.image_kind)} · ${shortId(option.image_artifact.artifact_id)}`;
  document.querySelector("#source-provenance").textContent = `SHA-256 ${option.image_artifact.sha256} · exact geometry ${geometryIdentity(option.geometry)} · signed access expires ${formatDate(option.image_download_expires_at)}`;
  renderLoadingControls(option);
  setView("source");
}

function renderLoadingControls(option) {
  const select = document.querySelector("#loading-control");
  select.replaceChildren(new Option("No loading control", ""));
  option.targets.forEach((target) => {
    const inferred = option.inferred_loading_control_target_ids.includes(target.target_id);
    select.append(new Option(`${target.label}${inferred ? " · inferred" : ""}`, target.target_id, false, inferred));
  });
  updateControlState();
}

function setView(view) {
  state.view = view;
  const attempt = state.selectedAttempt;
  const option = currentOption();
  const overlay = view === "overlay" && attempt;
  document.querySelector("#show-source").classList.toggle("active", !overlay);
  document.querySelector("#show-overlay").classList.toggle("active", Boolean(overlay));
  document.querySelector("#show-overlay").disabled = !attempt;
  if (!option && !overlay) return;
  visual.hidden = false; viewerMessage.hidden = true;
  geometryLayer.hidden = Boolean(overlay);
  sourceImage.onload = () => {
    const geometry = overlay ? attempt.result.input : option;
    const reference = geometry.lanes[0]?.region;
    if (reference) geometryLayer.setAttribute("viewBox", `0 0 ${reference.canvas_width} ${reference.canvas_height}`);
    if (!overlay) renderGeometry(option);
  };
  sourceImage.onerror = () => { visual.hidden = true; viewerMessage.hidden = false; viewerMessage.textContent = "The signed image URL could not be decoded."; };
  sourceImage.src = overlay ? attempt.overlay_download_url : option.image_download_url;
}

function renderGeometry(option) {
  geometryLayer.replaceChildren();
  option.lanes.forEach((item) => geometryLayer.append(regionRect(item.region, "lane", `Lane ${item.lane_index}`)));
  option.targets.forEach((item) => geometryLayer.append(regionRect(item.region, item.is_loading_control ? "control" : "target", item.label)));
  option.bands.forEach((item) => geometryLayer.append(regionRect(item.region, "band", "Band")));
}

function regionRect(region, className, label) {
  const element = document.createElementNS("http://www.w3.org/2000/svg", "rect");
  ["x","y","width","height"].forEach((key) => element.setAttribute(key, region[key]));
  element.setAttribute("class", className);
  const title = document.createElementNS("http://www.w3.org/2000/svg", "title"); title.textContent = label; element.append(title);
  return element;
}

async function runAnalysis() {
  const option = currentOption(); if (!option || state.running) return;
  state.running = true; runButton.disabled = true; runButton.textContent = "Running…"; hideNotice(); setStatus("Computing deterministic intensities…");
  try {
    const response = await fetch(`/api/v1/evaluation-cases/${caseId}/densitometry-runs`, {
      method:"POST", headers:{"Content-Type":"application/json"},
      body:JSON.stringify({ schema_version:"1.0", image_artifact_id:option.image_artifact.artifact_id, geometry:option.geometry, loading_control_target_id:optionalValue("#loading-control"), configuration:configurationPayload() }),
    });
    if (!response.ok) throw new Error(await responseMessage(response));
    const attempt = await response.json();
    await loadWorkbench(attempt.invocation_id);
    setStatus("Analysis published");
  } catch (error) { showNotice(error.message || "Densitometry failed."); setStatus("Run failed"); }
  finally { state.running = false; runButton.disabled = !currentOption(); runButton.textContent = "Run deterministic analysis"; }
}

function configurationPayload() {
  return {
    schema_version:"1.0", background_method:document.querySelector("#background-method").value,
    normalization_method:document.querySelector("#normalization-method").value,
    background_percentile:Number(document.querySelector("#background-percentile").value), local_border_pixels:Number(document.querySelector("#local-border").value),
    saturation_black_level:4, saturation_fraction_threshold:0.05, minimum_band_width_pixels:3, minimum_band_height_pixels:2, minimum_band_area_pixels:9,
    uneven_background_cv_threshold:0.35, lane_boundaries_reviewed:document.querySelector("#lane-reviewed").checked, exposure_known:document.querySelector("#exposure-known").checked,
  };
}

function renderAttempts() {
  const container = document.querySelector("#attempt-list"); container.replaceChildren();
  if (!state.workbench.attempts.length) { container.append(message("No densitometry attempts yet.")); return; }
  state.workbench.attempts.forEach((attempt) => {
    const card = document.createElement("div"); card.className = "attempt"; card.classList.toggle("active", state.selectedAttempt?.invocation_id === attempt.invocation_id);
    const title = document.createElement("strong"); title.textContent = `${humanize(attempt.result.suitability)} · ${attempt.result.measurements.length} values`;
    const meta = document.createElement("small"); meta.textContent = `${formatDate(attempt.created_at)} · ${shortId(attempt.invocation_id)}${attempt.replay_of_invocation_id ? " · replay" : ""}`;
    const actions = document.createElement("div"); actions.className = "actions";
    const view = document.createElement("button"); view.type = "button"; view.textContent = "View"; view.addEventListener("click", () => selectAttempt(attempt));
    const replay = document.createElement("button"); replay.type = "button"; replay.textContent = "Replay"; replay.addEventListener("click", () => replayAttempt(attempt, replay));
    actions.append(view, replay); card.append(title, meta, actions); container.append(card);
  });
}

async function replayAttempt(attempt, button) {
  button.disabled = true; setStatus("Replaying exact inputs…"); hideNotice();
  try {
    const response = await fetch(`/api/v1/densitometry-invocations/${attempt.invocation_id}/replays`, { method:"POST", headers:{"Content-Type":"application/json"}, body:JSON.stringify({schema_version:"1.0"}) });
    if (!response.ok) throw new Error(await responseMessage(response));
    const replay = await response.json(); await loadWorkbench(replay.invocation_id); setStatus("Replay published");
  } catch (error) { showNotice(error.message || "Replay failed."); setStatus("Replay failed"); button.disabled = false; }
}

function selectAttempt(attempt) { state.selectedAttempt = attempt; renderResult(attempt); renderAttempts(); document.querySelector("#show-overlay").disabled = false; setView("overlay"); }

function renderResult(attempt) {
  const summary = document.querySelector("#result-summary"); const qc = document.querySelector("#qc-list"); const body = document.querySelector("#measurement-table");
  summary.replaceChildren(); qc.replaceChildren(); body.replaceChildren();
  if (!attempt) { summary.textContent = "Run an analysis or select a historic attempt."; qc.append(message("No result selected.")); return; }
  const result = attempt.result; const badge = document.createElement("span"); badge.className = "suitability"; badge.textContent = humanize(result.suitability); summary.append(badge);
  const meta = document.createElement("dl"); meta.className = "result-meta";
  [["Tool",`${result.tool.name} ${result.tool.version}`],["Source hash",result.input.image_artifact.sha256],["Geometry",geometryIdentity(result.input.geometry)],["Input hash",result.reproducibility.input_sha256],["Config hash",result.reproducibility.configuration_sha256],["Result ID",result.result_id]].forEach(([term,value]) => { const dt=document.createElement("dt");dt.textContent=term;const dd=document.createElement("dd");dd.textContent=value;meta.append(dt,dd); }); summary.append(meta);
  const flags = [...result.global_qc_flags, ...result.measurements.flatMap((item) => item.qc_flags)];
  if (!flags.length) qc.append(message("No QC warnings for the configured thresholds."));
  flags.forEach((flag) => { const item=document.createElement("div");item.className=`qc ${flag.severity}`;const title=document.createElement("strong");title.textContent=`${humanize(flag.code)} · ${flag.severity}`;const detail=document.createElement("span");detail.textContent=flag.message;item.append(title,detail);qc.append(item); });
  result.measurements.forEach((measurement) => { const row=document.createElement("tr"); [measurement.lane_label || measurement.lane_index,measurement.target_label,number(measurement.raw_intensity),number(measurement.background_estimate),number(measurement.corrected_intensity),measurement.normalized_intensity == null ? "—" : number(measurement.normalized_intensity)].forEach((value) => { const cell=document.createElement("td");cell.textContent=value;row.append(cell); }); body.append(row); });
}

function updateControlState() { document.querySelector("#loading-control").disabled = document.querySelector("#normalization-method").value === "none"; document.querySelector("#local-border").disabled = document.querySelector("#background-method").value !== "local_border"; document.querySelector("#background-percentile").disabled = document.querySelector("#background-method").value === "none"; }
function currentOption() { return state.workbench?.geometry_options[state.optionIndex] || null; }
function optionalValue(selector) { const value=document.querySelector(selector).value; return value || null; }
function geometryIdentity(value) { return value.source_type === "prediction" ? `prediction ${value.prediction_id}` : `reviewer revision ${value.annotation_revision_id}`; }
function humanize(value) { return String(value).replaceAll("_"," ").replace(/\b\w/g,(letter) => letter.toUpperCase()); }
function shortId(value) { return String(value).slice(0,8); }
function number(value) { return Number(value).toLocaleString(undefined,{maximumFractionDigits:6}); }
function formatDate(value) { return new Intl.DateTimeFormat(undefined,{dateStyle:"medium",timeStyle:"short"}).format(new Date(value)); }
function message(text) { const element=document.createElement("p");element.className="muted";element.textContent=text;return element; }
function showNotice(text) { notice.hidden=false;notice.textContent=text; }
function hideNotice() { notice.hidden=true;notice.textContent=""; }
function setStatus(text) { document.querySelector("#status").textContent=text; }
async function responseMessage(response) { try { const payload=await response.json();return typeof payload.detail === "string" ? payload.detail : JSON.stringify(payload.detail); } catch { return `${response.status} ${response.statusText}`; } }
