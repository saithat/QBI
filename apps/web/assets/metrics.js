"use strict";

const state = { runs: [], candidate: null, baseline: null };
const runList = document.querySelector("#run-list");
const baselineSelect = document.querySelector("#baseline-select");
const candidateSelect = document.querySelector("#candidate-select");
const compareButton = document.querySelector("#compare-button");
const categorySelect = document.querySelector("#calibration-category");

document.querySelector("#filters").addEventListener("submit", applyFilters);
baselineSelect.addEventListener("change", selectRuns);
candidateSelect.addEventListener("change", selectRuns);
compareButton.addEventListener("click", compareRuns);
categorySelect.addEventListener("change", loadCalibration);

loadRuns();

async function loadRuns() {
  runList.setAttribute("aria-busy", "true");
  try {
    const query = new URLSearchParams();
    const pageQuery = new URLSearchParams(window.location.search);
    ["dataset_name", "pipeline_name"].forEach((key) => {
      const value = pageQuery.get(key);
      if (value) query.set(key, value);
      const input = document.querySelector(`[name=${key}]`);
      input.value = value || "";
    });
    query.set("limit", "100");
    const response = await fetch(`/api/v1/evaluation-metric-runs?${query}`);
    if (!response.ok) throw new Error(await responseMessage(response));
    state.runs = (await response.json()).runs;
    renderRunList();
    populateSelectors();
    restoreSelection(pageQuery);
  } catch (error) {
    showNotice(error.message || "Metric history could not be loaded.", "error");
    runList.textContent = "Metric history unavailable.";
  } finally {
    runList.setAttribute("aria-busy", "false");
  }
}

function renderRunList() {
  runList.replaceChildren();
  if (!state.runs.length) {
    runList.textContent = "No metric runs match these filters.";
    return;
  }
  state.runs.forEach((run) => {
    const button = document.createElement("button");
    button.type = "button";
    button.className = "run-card";
    button.dataset.runId = run.metric_run_id;
    const score = document.createElement("b");
    score.className = "run-score";
    score.textContent = percent(run.overall.composite_score);
    const title = document.createElement("strong");
    title.textContent = `${run.pipeline_name} · ${run.pipeline_version}`;
    const dataset = document.createElement("span");
    dataset.textContent = `${run.dataset_name}@${run.dataset_version}`;
    const date = document.createElement("span");
    date.textContent = formatDate(run.created_at);
    button.append(score, title, dataset, date);
    button.addEventListener("click", () => {
      candidateSelect.value = run.metric_run_id;
      selectRuns();
    });
    runList.append(button);
  });
}

function populateSelectors() {
  [baselineSelect, candidateSelect].forEach((select) => {
    select.replaceChildren(new Option("Select run", ""));
    state.runs.forEach((run) => {
      select.add(new Option(`${run.pipeline_version} · ${percent(run.overall.composite_score)}`, run.metric_run_id));
    });
  });
}

function restoreSelection(query) {
  const candidate = query.get("candidate");
  const baseline = query.get("baseline");
  candidateSelect.value = state.runs.some((run) => run.metric_run_id === candidate)
    ? candidate
    : (state.runs[0]?.metric_run_id || "");
  baselineSelect.value = state.runs.some((run) => run.metric_run_id === baseline)
    ? baseline
    : (state.runs[1]?.metric_run_id || "");
  selectRuns();
}

function selectRuns() {
  state.candidate = state.runs.find((run) => run.metric_run_id === candidateSelect.value) || null;
  state.baseline = state.runs.find((run) => run.metric_run_id === baselineSelect.value) || null;
  compareButton.disabled = !state.candidate || !state.baseline || state.candidate.metric_run_id === state.baseline.metric_run_id;
  document.querySelectorAll(".run-card").forEach((card) => card.classList.toggle("active", card.dataset.runId === state.candidate?.metric_run_id));
  updateUrl();
  renderCandidate();
  clearComparison();
  loadCalibration();
}

function renderCandidate() {
  const run = state.candidate;
  const grid = document.querySelector("#metric-grid");
  grid.replaceChildren();
  if (!run) {
    grid.append(messageCard("Choose a candidate run to load scores."));
    document.querySelector("#candidate-label").textContent = "No candidate selected";
    document.querySelector("#dataset-label").textContent = "Select a metric run to inspect.";
    document.querySelector("#group-grid").replaceChildren(muted("Select a candidate to inspect grouped metrics."));
    return;
  }
  document.querySelector("#candidate-label").textContent = `${run.pipeline_name}@${run.pipeline_version}`;
  document.querySelector("#dataset-label").textContent = `${run.dataset_name}@${run.dataset_version} · sha256:${run.dataset_sha256.slice(0, 12)}… · scorer ${run.scorer_name}@${run.scorer_version}`;
  const metrics = run.overall;
  [
    ["Composite", metrics.composite_score, "End-to-end score", true],
    ["Exact fields", metrics.fields.exact.f1, `${counts(metrics.fields.exact)} · F1`],
    ["Normalized fields", metrics.fields.normalized.f1, `${counts(metrics.fields.normalized)} · F1`],
    ["Geometry", metrics.geometry.match.f1, `${percent(metrics.geometry.mean_iou)} mean IoU`],
    ["Relationships", metrics.relationships.f1, `${counts(metrics.relationships)} · F1`],
    ["Citations", metrics.citations.f1, `${percent(metrics.citations.correctness)} correct`],
    ["Provenance", metrics.provenance.completeness, `${metrics.provenance.observations_with_evidence}/${metrics.provenance.predicted_observations} sourced`],
    ["Numerical", metrics.numerical.within_tolerance, `${number(metrics.numerical.mean_absolute_error)} MAE`],
    ["Ranking", metrics.ranking.ndcg_at_k, `${percent(metrics.ranking.recall_at_k)} recall@k`],
  ].forEach(([label, value, detail, primary]) => grid.append(metricCard(label, value, detail, primary)));
  renderGroups(run.groups);
}

async function loadCalibration() {
  if (!state.candidate) {
    renderCalibration(null);
    return;
  }
  try {
    const query = categorySelect.value ? `?category=${encodeURIComponent(categorySelect.value)}` : "";
    const response = await fetch(`/api/v1/evaluation-metric-runs/${state.candidate.metric_run_id}/calibration${query}`);
    if (!response.ok) throw new Error(await responseMessage(response));
    renderCalibration((await response.json()).calibration);
  } catch (error) {
    renderCalibration(null);
    showNotice(error.message || "Calibration could not be loaded.", "error");
  }
}

function renderCalibration(calibration) {
  document.querySelector("#brier-score").textContent = calibration ? number(calibration.brier_score) : "—";
  document.querySelector("#ece-score").textContent = calibration ? number(calibration.expected_calibration_error) : "—";
  document.querySelector("#observation-count").textContent = calibration ? calibration.observations : "—";
  renderReliability(calibration?.buckets || []);
  const body = document.querySelector("#coverage-body");
  body.replaceChildren();
  if (!calibration?.coverage_curve.length) {
    const row = document.createElement("tr");
    const cell = document.createElement("td");
    cell.colSpan = 5;
    cell.className = "muted";
    cell.textContent = "No confidence observations for this slice.";
    row.append(cell);
    body.append(row);
    return;
  }
  calibration.coverage_curve.forEach((point) => {
    const row = document.createElement("tr");
    [number(point.threshold), point.retained, percent(point.coverage), percent(point.precision), percent(point.risk)].forEach((value) => {
      const cell = document.createElement("td");
      cell.textContent = value;
      row.append(cell);
    });
    body.append(row);
  });
}

function renderReliability(buckets) {
  const svg = document.querySelector("#reliability-chart");
  svg.replaceChildren();
  const NS = "http://www.w3.org/2000/svg";
  const left = 43, top = 15, width = 450, height = 215;
  for (let value = 0; value <= 1; value += .25) {
    const y = top + height - value * height;
    svg.append(svgLine(left, y, left + width, y, "chart-grid"));
    const label = document.createElementNS(NS, "text");
    label.setAttribute("x", "6"); label.setAttribute("y", String(y + 3)); label.setAttribute("class", "chart-label");
    label.textContent = value.toFixed(2);
    svg.append(label);
  }
  svg.append(svgLine(left, top + height, left + width, top + height, "chart-axis"));
  svg.append(svgLine(left, top, left, top + height, "chart-axis"));
  svg.append(svgLine(left, top + height, left + width, top, "chart-perfect"));
  if (!buckets.length) return;
  const gap = 5;
  const barWidth = width / buckets.length - gap;
  buckets.forEach((bucket, index) => {
    const bar = document.createElementNS(NS, "rect");
    const barHeight = bucket.accuracy * height;
    bar.setAttribute("x", String(left + index * (width / buckets.length) + gap / 2));
    bar.setAttribute("y", String(top + height - barHeight));
    bar.setAttribute("width", String(Math.max(1, barWidth)));
    bar.setAttribute("height", String(barHeight));
    bar.setAttribute("rx", "2"); bar.setAttribute("class", "chart-bar");
    const title = document.createElementNS(NS, "title");
    title.textContent = `${bucket.count} observations · ${percent(bucket.average_confidence)} confidence · ${percent(bucket.accuracy)} accuracy`;
    bar.append(title); svg.append(bar);
  });
}

async function compareRuns() {
  if (!state.baseline || !state.candidate) return;
  compareButton.disabled = true;
  try {
    const response = await fetch("/api/v1/evaluation-pipeline-comparisons", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        schema_version: "1.0",
        baseline_metric_run_id: state.baseline.metric_run_id,
        candidate_metric_run_id: state.candidate.metric_run_id,
        minimum_delta: 0.01,
      }),
    });
    if (!response.ok) throw new Error(await responseMessage(response));
    renderComparison(await response.json());
    showNotice("Comparison complete. Historic metric runs were not modified.", "success");
  } catch (error) {
    showNotice(error.message || "Pipeline comparison failed.", "error");
  } finally {
    compareButton.disabled = !state.baseline || !state.candidate || state.baseline.metric_run_id === state.candidate.metric_run_id;
  }
}

function renderComparison(comparison) {
  const delta = document.querySelector("#overall-delta");
  delta.textContent = signedPercent(comparison.overall_delta);
  delta.className = `delta ${comparison.overall_delta < 0 ? "negative" : comparison.overall_delta > 0 ? "positive" : "neutral"}`;
  const regressions = comparison.cases.filter((item) => item.outcome === "regression");
  const improvements = comparison.cases.filter((item) => item.outcome === "improvement");
  document.querySelector("#regression-count").textContent = regressions.length;
  document.querySelector("#improvement-count").textContent = improvements.length;
  renderCaseLinks(document.querySelector("#regressions"), regressions, false);
  renderCaseLinks(document.querySelector("#improvements"), improvements, true);
}

function renderCaseLinks(container, cases, improvement) {
  container.replaceChildren();
  if (!cases.length) {
    container.append(muted(improvement ? "No improvements above the threshold." : "No regressions above the threshold."));
    return;
  }
  cases.forEach((item) => {
    const link = document.createElement("a");
    link.className = `case-link${improvement ? " improvement" : ""}`;
    link.href = item.workbench_url;
    const id = document.createElement("code"); id.textContent = item.case_id;
    const delta = document.createElement("strong"); delta.textContent = signedPercent(item.delta);
    link.append(id, delta); container.append(link);
  });
}

function renderGroups(groups) {
  const grid = document.querySelector("#group-grid");
  grid.replaceChildren();
  groups.forEach((group) => {
    const card = document.createElement("article"); card.className = "group-card";
    const dimension = document.createElement("span"); dimension.textContent = humanize(group.dimension);
    const value = document.createElement("strong"); value.textContent = group.value;
    const footer = document.createElement("footer");
    const cases = document.createElement("span"); cases.textContent = `${group.case_count} case${group.case_count === 1 ? "" : "s"}`;
    const score = document.createElement("b"); score.textContent = percent(group.metrics.composite_score);
    footer.append(cases, score); card.append(dimension, value, footer); grid.append(card);
  });
}

function clearComparison() {
  document.querySelector("#overall-delta").textContent = "—";
  document.querySelector("#overall-delta").className = "delta neutral";
  document.querySelector("#regression-count").textContent = "0";
  document.querySelector("#improvement-count").textContent = "0";
  document.querySelector("#regressions").replaceChildren(muted("Run a comparison to inspect regressions."));
  document.querySelector("#improvements").replaceChildren(muted("Run a comparison to inspect improvements."));
}

function applyFilters(event) {
  event.preventDefault();
  const query = new URLSearchParams();
  new FormData(event.currentTarget).forEach((value, key) => { if (value) query.set(key, value); });
  window.history.replaceState({}, "", `${window.location.pathname}?${query}`);
  loadRuns();
}

function updateUrl() {
  const query = new URLSearchParams(window.location.search);
  if (state.baseline) query.set("baseline", state.baseline.metric_run_id); else query.delete("baseline");
  if (state.candidate) query.set("candidate", state.candidate.metric_run_id); else query.delete("candidate");
  window.history.replaceState({}, "", `${window.location.pathname}?${query}`);
}

function metricCard(label, value, detail, primary = false) {
  const card = document.createElement("article"); card.className = `metric-card${primary ? " primary" : ""}`;
  const name = document.createElement("span"); name.textContent = label;
  const score = document.createElement("strong"); score.textContent = percent(value);
  const note = document.createElement("small"); note.textContent = detail;
  card.append(name, score, note); return card;
}

function messageCard(message) { const card = document.createElement("article"); card.className = "metric-card empty"; card.textContent = message; return card; }
function muted(message) { const node = document.createElement("p"); node.className = "muted"; node.textContent = message; return node; }
function svgLine(x1, y1, x2, y2, className) { const line = document.createElementNS("http://www.w3.org/2000/svg", "line"); line.setAttribute("x1", x1); line.setAttribute("y1", y1); line.setAttribute("x2", x2); line.setAttribute("y2", y2); line.setAttribute("class", className); return line; }
function percent(value) { return `${(Number(value) * 100).toFixed(1)}%`; }
function signedPercent(value) { const numberValue = Number(value); return `${numberValue > 0 ? "+" : ""}${(numberValue * 100).toFixed(1)} pp`; }
function number(value) { return Number(value).toFixed(3); }
function counts(metric) { return `${metric.true_positive} TP · ${metric.false_positive} FP · ${metric.false_negative} FN`; }
function humanize(value) { return String(value).replaceAll("_", " ").replace(/\b\w/g, (character) => character.toUpperCase()); }
function formatDate(value) { return new Intl.DateTimeFormat(undefined, { dateStyle: "medium", timeStyle: "short" }).format(new Date(value)); }
function showNotice(message, kind = "") { const notice = document.querySelector("#notice"); notice.textContent = message; notice.className = `notice${kind ? ` ${kind}` : ""}`; }
async function responseMessage(response) { try { const payload = await response.json(); return payload.detail || `Request failed (${response.status})`; } catch (_) { return `Request failed (${response.status})`; } }
