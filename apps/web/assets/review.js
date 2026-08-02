"use strict";

const form = document.querySelector("#filter-form");
const list = document.querySelector("#queue-list");
const template = document.querySelector("#case-template");
const notice = document.querySelector("#notice");
const resultCount = document.querySelector("#result-count");
const pageStatus = document.querySelector("#page-status");
const previousPage = document.querySelector("#previous-page");
const nextPage = document.querySelector("#next-page");
const nextCase = document.querySelector("#next-case");
const assignCase = document.querySelector("#assign-case");
const openWorkbench = document.querySelector("#open-workbench");
const openAnnotation = document.querySelector("#open-annotation");
const savedView = document.querySelector("#saved-view");
const reviewerOutput = document.querySelector("#reviewer-id");

const PAGE_SIZE = 50;
const FILTER_NAMES = [
  "review_status", "dataset_id", "assay_type", "source", "prediction_version",
  "confidence_min", "confidence_max", "error_category", "reviewer_id",
  "missing_provenance", "validation_warnings", "gold_eligible",
  "model_disagreement", "regression_status",
];

let state = { items: [], total: 0, offset: 0, selectedIndex: -1, loading: false };
const reviewerId = localReviewerId();
reviewerOutput.textContent = reviewerId;

form.addEventListener("submit", (event) => {
  event.preventDefault();
  writeFiltersToUrl(0);
  loadQueue();
});

document.querySelector("#clear-filters").addEventListener("click", () => {
  form.reset();
  savedView.value = "";
  writeFiltersToUrl(0, null);
  loadQueue();
});

document.querySelector("#save-view").addEventListener("click", saveCurrentView);
savedView.addEventListener("change", applySavedView);
previousPage.addEventListener("click", () => changePage(Math.max(0, state.offset - PAGE_SIZE)));
nextPage.addEventListener("click", () => changePage(state.offset + PAGE_SIZE));
nextCase.addEventListener("click", () => moveSelection(1));
assignCase.addEventListener("click", assignSelectedCase);
openWorkbench.addEventListener("click", openSelectedWorkbench);
openAnnotation.addEventListener("click", openSelectedAnnotation);
window.addEventListener("popstate", () => {
  readFiltersFromUrl();
  loadQueue();
});
document.addEventListener("keydown", handleShortcut);

readFiltersFromUrl();
Promise.all([loadSavedViews(), loadQueue()]);

async function loadQueue() {
  if (state.loading) return;
  state.loading = true;
  list.setAttribute("aria-busy", "true");
  hideNotice();
  const params = new URLSearchParams(window.location.search);
  params.delete("case");
  params.set("limit", String(PAGE_SIZE));
  try {
    const response = await fetch(`/api/v1/review-queue?${params.toString()}`);
    if (!response.ok) throw new Error(await responseMessage(response));
    const page = await response.json();
    state.items = page.items;
    state.total = page.total;
    state.offset = page.offset;
    renderQueue();
  } catch (error) {
    state.items = [];
    state.total = 0;
    renderQueue();
    showNotice(error.message || "Review queue could not be loaded.", true);
  } finally {
    state.loading = false;
    list.setAttribute("aria-busy", "false");
  }
}

function renderQueue() {
  list.replaceChildren();
  resultCount.textContent = state.total.toLocaleString();
  const selectedId = new URLSearchParams(window.location.search).get("case");
  state.selectedIndex = selectedId ? state.items.findIndex((item) => item.case_id === selectedId) : -1;

  if (!state.items.length) {
    const empty = document.createElement("div");
    empty.className = "empty-state";
    empty.textContent = "No cases match these filters.";
    list.append(empty);
  } else {
    state.items.forEach((item, index) => list.append(renderCase(item, index)));
  }

  const first = state.total ? state.offset + 1 : 0;
  const last = Math.min(state.offset + state.items.length, state.total);
  pageStatus.textContent = `${first.toLocaleString()}–${last.toLocaleString()} of ${state.total.toLocaleString()}`;
  previousPage.disabled = state.offset === 0;
  nextPage.disabled = state.offset + state.items.length >= state.total;
  nextCase.disabled = !state.items.length;
  assignCase.disabled = state.selectedIndex < 0;
  openWorkbench.disabled = state.selectedIndex < 0;
  openAnnotation.disabled = state.selectedIndex < 0;
}

function renderCase(item, index) {
  const card = template.content.firstElementChild.cloneNode(true);
  card.dataset.caseId = item.case_id;
  card.classList.toggle("selected", index === state.selectedIndex);
  card.setAttribute("aria-label", `${humanize(item.review_status)}: ${item.case_key}`);
  card.querySelector(".status-pill").textContent = humanize(item.review_status);
  card.querySelector(".status-pill").classList.add(item.review_status);
  card.querySelector(".case-key").textContent = item.case_key;
  card.querySelector(".source-label").textContent = item.source_label || "Source unavailable";
  card.querySelector(".case-metadata").textContent = [
    item.prediction_version ? `Prediction ${item.prediction_version}` : "No prediction",
    item.confidence == null ? "No confidence" : `${Math.round(item.confidence * 100)}% confidence`,
    item.dataset_id ? `Dataset ${shortId(item.dataset_id)}` : "No dataset",
  ].join(" · ");

  const signals = card.querySelector(".case-signals");
  addSignal(signals, `${item.warning_count} warning${item.warning_count === 1 ? "" : "s"}`, item.warning_count ? "warning" : "good");
  if (item.missing_provenance) addSignal(signals, "Missing provenance", "danger");
  if (item.model_disagreement) addSignal(signals, "Model disagreement", "warning");
  if (item.gold_eligible) addSignal(signals, "Gold eligible", "good");
  if (item.regression_status !== "not_evaluated") addSignal(signals, humanize(item.regression_status), item.regression_status === "regressed" ? "danger" : "");
  item.error_categories.forEach((category) => addSignal(signals, category, ""));

  const review = card.querySelector(".case-review");
  const title = document.createElement("strong");
  title.textContent = item.exclusively_assigned
    ? "Exclusively assigned"
    : item.active_reviewer_ids.length
      ? `${item.active_reviewer_ids.length} active reviewer${item.active_reviewer_ids.length === 1 ? "" : "s"}`
      : "Unassigned";
  const detail = document.createElement("span");
  detail.textContent = item.last_reviewer_id
    ? `Last review ${shortId(item.last_reviewer_id)} · ${formatDate(item.last_reviewed_at)}`
    : `Updated ${formatDate(item.updated_at)}`;
  review.append(title, detail);

  card.addEventListener("click", () => selectCase(index));
  card.addEventListener("keydown", (event) => {
    if (event.key === "Enter" || event.key === " ") {
      event.preventDefault();
      selectCase(index);
    }
  });
  if (item.thumbnail_artifact_id) observeThumbnail(card, item.thumbnail_artifact_id);
  return card;
}

function addSignal(container, label, variant) {
  const signal = document.createElement("span");
  signal.className = `signal ${variant}`.trim();
  signal.textContent = label;
  container.append(signal);
}

function selectCase(index, focus = false) {
  if (index < 0 || index >= state.items.length) return;
  state.selectedIndex = index;
  const params = new URLSearchParams(window.location.search);
  params.set("case", state.items[index].case_id);
  history.replaceState(null, "", `${window.location.pathname}?${params.toString()}`);
  document.querySelectorAll(".case-card").forEach((card, cardIndex) => {
    card.classList.toggle("selected", cardIndex === index);
  });
  assignCase.disabled = false;
  openWorkbench.disabled = false;
  openAnnotation.disabled = false;
  if (focus) document.querySelectorAll(".case-card")[index]?.focus();
}

function moveSelection(delta) {
  if (!state.items.length) return;
  const candidate = state.selectedIndex < 0 ? 0 : state.selectedIndex + delta;
  if (candidate >= state.items.length && state.offset + state.items.length < state.total) {
    changePage(state.offset + PAGE_SIZE, true);
    return;
  }
  if (candidate < 0 && state.offset > 0) {
    changePage(Math.max(0, state.offset - PAGE_SIZE), true, -1);
    return;
  }
  selectCase(Math.max(0, Math.min(candidate, state.items.length - 1)), true);
}

async function changePage(offset, selectAfterLoad = false, selectedIndex = 0) {
  const params = new URLSearchParams(window.location.search);
  params.delete("case");
  if (offset) params.set("offset", String(offset)); else params.delete("offset");
  history.pushState(null, "", `${window.location.pathname}?${params.toString()}`);
  await loadQueue();
  if (selectAfterLoad && state.items.length) {
    selectCase(selectedIndex < 0 ? state.items.length - 1 : selectedIndex, true);
  }
}

async function assignSelectedCase() {
  const item = state.items[state.selectedIndex];
  if (!item) return;
  assignCase.disabled = true;
  try {
    const response = await fetch(`/api/v1/evaluation-cases/${item.case_id}/assignments`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ reviewer_id: reviewerId, exclusive: true }),
    });
    if (!response.ok) throw new Error(await responseMessage(response));
    showNotice(`Case ${shortId(item.case_id)} assigned to this reviewer.`);
    await loadQueue();
  } catch (error) {
    showNotice(error.message || "The case could not be assigned.", true);
  } finally {
    assignCase.disabled = state.selectedIndex < 0;
  }
}

function openSelectedWorkbench() {
  const item = state.items[state.selectedIndex];
  if (item) window.location.assign(`/workbench/${item.case_id}`);
}

function openSelectedAnnotation() {
  const item = state.items[state.selectedIndex];
  if (item) window.location.assign(`/annotate/${item.case_id}`);
}

async function loadSavedViews() {
  try {
    const response = await fetch(`/api/v1/review-views?owner_id=${encodeURIComponent(reviewerId)}`);
    if (!response.ok) throw new Error(await responseMessage(response));
    const payload = await response.json();
    savedView.replaceChildren(new Option("Current filters", ""));
    payload.views.forEach((view) => {
      const option = new Option(view.name, view.view_id);
      option.dataset.filters = JSON.stringify(view.filters);
      savedView.append(option);
    });
  } catch (error) {
    showNotice(error.message || "Saved views could not be loaded.", true);
  }
}

async function saveCurrentView() {
  const name = window.prompt("Name this filter view:");
  if (!name || !name.trim()) return;
  try {
    const response = await fetch("/api/v1/review-views", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ owner_id: reviewerId, name: name.trim(), filters: filtersForApi() }),
    });
    if (!response.ok) throw new Error(await responseMessage(response));
    const view = await response.json();
    await loadSavedViews();
    savedView.value = view.view_id;
    showNotice(`Saved “${view.name}”.`);
  } catch (error) {
    showNotice(error.message || "The view could not be saved.", true);
  }
}

function applySavedView() {
  const option = savedView.selectedOptions[0];
  if (!option?.dataset.filters) return;
  const filters = JSON.parse(option.dataset.filters);
  form.reset();
  const mapping = { review_statuses: "review_status", source_query: "source", has_validation_warnings: "validation_warnings" };
  Object.entries(filters).forEach(([name, value]) => {
    if (name === "schema_version" || value == null || value === "" || (Array.isArray(value) && !value.length)) return;
    const inputName = mapping[name] || name;
    const input = form.elements.namedItem(inputName);
    if (!input) return;
    input.value = Array.isArray(value) ? value[0] || "" : String(value);
  });
  writeFiltersToUrl(0);
  loadQueue();
}

function readFiltersFromUrl() {
  const params = new URLSearchParams(window.location.search);
  FILTER_NAMES.forEach((name) => {
    const input = form.elements.namedItem(name);
    if (input) input.value = params.get(name) || "";
  });
}

function writeFiltersToUrl(offset = 0, caseId = undefined) {
  const params = new URLSearchParams();
  FILTER_NAMES.forEach((name) => {
    const value = form.elements.namedItem(name)?.value.trim();
    if (value) params.set(name, value);
  });
  if (offset) params.set("offset", String(offset));
  if (caseId === undefined) {
    const currentCase = new URLSearchParams(window.location.search).get("case");
    if (currentCase) params.set("case", currentCase);
  } else if (caseId) {
    params.set("case", caseId);
  }
  const query = params.toString();
  history.pushState(null, "", query ? `${window.location.pathname}?${query}` : window.location.pathname);
}

function filtersForApi() {
  const filters = { schema_version: "1.0", review_statuses: [] };
  FILTER_NAMES.forEach((name) => {
    const value = form.elements.namedItem(name)?.value.trim();
    if (!value) return;
    if (name === "review_status") filters.review_statuses = [value];
    else if (name === "source") filters.source_query = value;
    else if (name === "validation_warnings") filters.has_validation_warnings = value === "true";
    else if (["missing_provenance", "gold_eligible", "model_disagreement"].includes(name)) filters[name] = value === "true";
    else if (["confidence_min", "confidence_max"].includes(name)) filters[name] = Number(value);
    else filters[name] = value;
  });
  return filters;
}

function observeThumbnail(card, artifactId) {
  const image = card.querySelector(".thumbnail");
  const observer = new IntersectionObserver(async (entries) => {
    if (!entries.some((entry) => entry.isIntersecting)) return;
    observer.disconnect();
    try {
      const response = await fetch(`/api/v1/artifacts/${artifactId}/download-url`, { method: "POST" });
      if (!response.ok) return;
      const payload = await response.json();
      image.addEventListener("load", () => image.classList.add("loaded"), { once: true });
      image.src = payload.url;
    } catch (_) {
      // The explicit placeholder remains visible when source access fails.
    }
  }, { rootMargin: "160px" });
  observer.observe(card);
}

function handleShortcut(event) {
  if (event.metaKey || event.ctrlKey || event.altKey) return;
  const editing = ["INPUT", "SELECT", "TEXTAREA"].includes(document.activeElement?.tagName);
  if (event.key === "/" && !editing) {
    event.preventDefault();
    form.elements.namedItem("source").focus();
  } else if (!editing && event.key.toLowerCase() === "j") {
    event.preventDefault();
    moveSelection(1);
  } else if (!editing && event.key.toLowerCase() === "k") {
    event.preventDefault();
    moveSelection(-1);
  } else if (!editing && event.key.toLowerCase() === "n") {
    event.preventDefault();
    moveSelection(1);
  } else if (!editing && event.key.toLowerCase() === "a") {
    event.preventDefault();
    assignSelectedCase();
  }
}

function localReviewerId() {
  const key = "hiveblot.localReviewerId";
  let value = localStorage.getItem(key);
  if (!value) {
    value = crypto.randomUUID();
    localStorage.setItem(key, value);
  }
  return value;
}

function showNotice(message, error = false) {
  notice.textContent = message;
  notice.classList.toggle("error", error);
  notice.hidden = false;
}

function hideNotice() {
  notice.hidden = true;
  notice.classList.remove("error");
}

async function responseMessage(response) {
  try {
    const payload = await response.json();
    return typeof payload.detail === "string" ? payload.detail : `Request failed (${response.status}).`;
  } catch (_) {
    return `Request failed (${response.status}).`;
  }
}

function humanize(value) {
  return value.replaceAll("_", " ").replace(/\b\w/g, (letter) => letter.toUpperCase());
}

function shortId(value) {
  return value ? value.slice(0, 8) : "—";
}

function formatDate(value) {
  if (!value) return "never";
  return new Intl.DateTimeFormat(undefined, { dateStyle: "medium", timeStyle: "short" }).format(new Date(value));
}
