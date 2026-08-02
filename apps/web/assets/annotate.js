"use strict";

const caseId = window.location.pathname.split("/").filter(Boolean).at(-1);
const reviewerId = localReviewerId();
const saveStatus = document.querySelector("#save-status");
const notice = document.querySelector("#editor-notice");
const rawJson = document.querySelector("#raw-json");
const rationale = document.querySelector("#rationale");
const reviewerNotes = document.querySelector("#reviewer-notes");
const predictionSelect = document.querySelector("#prediction-select");

const OBSERVATION_STATES = ["present", "absent", "unknown", "ambiguous", "not_applicable"];
const COLLECTIONS = [
  "proteins", "biological_contexts", "treatments", "lane_conditions",
  "antibodies", "molecular_weights", "replicates",
];
const DEFINITIONS = {
  proteins: {
    title: "Protein",
    fields: [
      selectField("role", "Role", ["target", "loading_control"]),
      textField("name", "Protein name"),
    ],
  },
  biological_contexts: {
    title: "Biological context",
    fields: [
      selectField("context_type", "Context type", ["cell_line", "tissue", "organism", "genotype"]),
      textField("name", "Name"),
    ],
  },
  treatments: {
    title: "Treatment",
    fields: [
      textField("name", "Treatment name", true),
      numberField("dose.value", "Dose"),
      textField("dose.unit", "Dose unit"),
      numberField("duration.value", "Duration"),
      textField("duration.unit", "Duration unit"),
    ],
  },
  lane_conditions: {
    title: "Lane",
    fields: [
      numberField("lane_index", "Lane index", false, true),
      textField("lane_label", "Lane label"),
      textField("condition_label", "Condition", true),
    ],
  },
  antibodies: {
    title: "Antibody",
    fields: [
      textField("name", "Antibody name", true),
      textField("vendor", "Vendor"),
      textField("catalog_number", "Catalog number"),
      textField("clone", "Clone"),
      textField("host_species", "Host species"),
      textField("dilution", "Dilution"),
    ],
  },
  molecular_weights: {
    title: "Molecular weight",
    fields: [numberField("value_kda", "Mass (kDa)", true)],
  },
  replicates: {
    title: "Replicate",
    fields: [
      numberField("biological_replicates", "Biological replicates", false, true),
      numberField("technical_replicates", "Technical replicates", false, true),
      textField("description", "Description", true),
    ],
  },
};
const RELATIONSHIPS = {
  lane_contains_protein: [isLane, isProtein],
  lane_receives_treatment: [isLane, (_, collection) => collection === "treatments"],
  lane_uses_biological_context: [isLane, (_, collection) => collection === "biological_contexts"],
  lane_has_replicate: [isLane, (_, collection) => collection === "replicates"],
  protein_uses_loading_control: [
    (entity, collection) => collection === "proteins" && entity.role === "target",
    (entity, collection) => collection === "proteins" && entity.role === "loading_control",
  ],
  protein_detected_by_antibody: [isProtein, (_, collection) => collection === "antibodies"],
  protein_has_molecular_weight: [isProtein, (_, collection) => collection === "molecular_weights"],
};

const state = {
  editor: null,
  draft: null,
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
document.querySelector("#open-evidence").href = `/workbench/${caseId}`;
document.querySelector("#spatial-link").href = `/spatial/${caseId}`;
document.querySelector("#save-now").addEventListener("click", () => saveNow());
document.querySelector("#accept-all").addEventListener("click", () => acceptPrediction([], true));
document.querySelector("#add-relationship").addEventListener("click", addRelationship);
document.querySelector("#apply-json").addEventListener("click", applyRawJson);
document.querySelectorAll(".add-entity").forEach((button) => button.addEventListener("click", addEntity));
predictionSelect.addEventListener("change", changePrediction);
reviewerNotes.addEventListener("input", () => {
  state.draft.reviewer_notes = optionalText(reviewerNotes.value);
  scheduleSave();
});
rationale.addEventListener("input", () => scheduleSave());
document.addEventListener("keydown", (event) => {
  if ((event.metaKey || event.ctrlKey) && event.key.toLowerCase() === "s") {
    event.preventDefault();
    saveNow();
  }
});

loadEditor();

async function loadEditor(predictionId = null) {
  setSaveStatus("Loading…", "saving");
  hideNotice();
  const params = new URLSearchParams({ reviewer_id: reviewerId });
  if (predictionId) params.set("prediction_id", predictionId);
  try {
    const response = await fetch(`/api/v1/evaluation-cases/${caseId}/structured-editor?${params}`);
    if (!response.ok) throw new Error(await responseMessage(response));
    state.editor = await response.json();
    state.draft = deepCopy(state.editor.draft_annotation);
    state.documentId = state.editor.annotation_document?.annotation_id || null;
    state.headRevisionId = state.editor.head_revision?.revision_id || null;
    state.selectedPredictionId = state.editor.selected_prediction_id;
    state.savedVersion = state.mutationVersion;
    rationale.value = state.editor.head_revision?.rationale || "";
    reviewerNotes.value = state.draft.reviewer_notes || "";
    renderAll();
    setSaveStatus(state.headRevisionId ? `Saved revision ${state.editor.head_revision.revision_number}` : "New review");
  } catch (error) {
    showNotice(error.message || "The structured editor could not be loaded.");
    setSaveStatus("Load failed", "error");
  }
}

function renderAll() {
  renderPredictionOptions();
  renderPredictionEntities();
  COLLECTIONS.forEach(renderCollection);
  renderRelationships();
  renderErrorCodes();
  renderDiff();
  renderRevisions();
  rawJson.value = JSON.stringify(state.draft, null, 2);
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
    const availability = prediction.structured_output_available ? "" : " · incompatible schema";
    const option = new Option(
      `${prediction.producer_name} ${prediction.producer_version} · v${prediction.prediction_schema_version}${availability}`,
      prediction.prediction_id,
    );
    option.disabled = !prediction.structured_output_available;
    option.selected = prediction.prediction_id === state.selectedPredictionId;
    predictionSelect.append(option);
  });
}

function renderPredictionEntities() {
  const container = document.querySelector("#prediction-entities");
  container.replaceChildren();
  const prediction = state.editor.prediction_annotation;
  document.querySelector("#accept-all").disabled = !prediction;
  if (!prediction) {
    container.append(emptyRow("No compatible structured prediction is available."));
    return;
  }
  entityEntries(prediction).forEach(({ entity, collection }) => {
    const card = document.createElement("article");
    card.className = "prediction-card";
    const name = document.createElement("strong");
    name.textContent = entityLabel(entity, collection);
    const kind = document.createElement("span");
    kind.textContent = humanize(collection.replace(/s$/, ""));
    const accept = document.createElement("button");
    accept.type = "button";
    accept.textContent = "Accept";
    accept.addEventListener("click", () => acceptPrediction([entity.entity_id], false));
    card.append(name, kind, accept);
    container.append(card);
  });
}

function renderCollection(collection) {
  const section = document.querySelector(`.entity-section[data-collection="${collection}"]`);
  const container = section.querySelector(".entity-list");
  container.replaceChildren();
  const entities = state.draft[collection];
  if (!entities.length) {
    container.append(emptyRow(`No ${humanize(collection)} recorded.`));
    return;
  }
  entities.forEach((entity, index) => container.append(renderEntity(collection, entity, index)));
}

function renderEntity(collection, entity, index) {
  const definition = DEFINITIONS[collection];
  const card = document.createElement("article");
  card.className = "entity-card";
  card.dataset.entityId = entity.entity_id;
  const header = document.createElement("div");
  header.className = "entity-card-header";
  const title = document.createElement("strong");
  title.textContent = `${definition.title} ${index + 1} · ${shortId(entity.entity_id)}`;
  const remove = document.createElement("button");
  remove.type = "button";
  remove.className = "delete-button";
  remove.textContent = "Remove";
  remove.addEventListener("click", () => deleteEntity(collection, entity.entity_id));
  header.append(title, remove);

  const grid = document.createElement("div");
  grid.className = "field-grid";
  grid.append(fieldControl(collection, entity, selectField("state", "State", OBSERVATION_STATES)));
  definition.fields.forEach((field) => grid.append(fieldControl(collection, entity, field)));
  grid.append(fieldControl(collection, entity, textField("original_extracted_text", "Original extracted text", true, true)));
  grid.append(fieldControl(collection, entity, textField("notes", "Reviewer notes", true, true)));
  card.append(header, grid);
  if (supportsCanonicalLookup(collection)) card.append(canonicalControls(collection, entity));
  return card;
}

function fieldControl(collection, entity, field) {
  const label = document.createElement("label");
  if (field.span) label.className = "span-2";
  label.append(document.createTextNode(field.label));
  const value = nestedValue(entity, field.key);
  let input;
  if (field.options) {
    input = document.createElement("select");
    field.options.forEach((optionValue) => input.append(new Option(humanize(optionValue), optionValue)));
    input.value = value ?? field.options[0];
  } else if (field.multiline) {
    input = document.createElement("textarea");
    input.rows = 2;
    input.value = value ?? "";
  } else {
    input = document.createElement("input");
    input.type = field.type;
    input.value = value ?? "";
    if (field.type === "number") {
      input.min = field.integer ? "1" : "0.000001";
      input.step = field.integer ? "1" : "any";
    }
  }
  input.addEventListener("input", () => {
    updateEntityField(collection, entity.entity_id, field, input.value);
  });
  label.append(input);
  return label;
}

function canonicalControls(collection, entity) {
  const wrapper = document.createElement("div");
  const row = document.createElement("div");
  row.className = "canonical-row";
  const status = document.createElement("span");
  status.textContent = entity.canonical_reference
    ? `${entity.canonical_reference.vocabulary}: ${entity.canonical_reference.label}`
    : "No canonical entity selected";
  const lookup = document.createElement("button");
  lookup.type = "button";
  lookup.textContent = "Look up";
  lookup.addEventListener("click", () => lookupCanonical(collection, entity, wrapper));
  row.append(status, lookup);
  if (entity.canonical_reference) {
    const clear = document.createElement("button");
    clear.type = "button";
    clear.textContent = "Clear";
    clear.addEventListener("click", () => {
      entity.canonical_reference = null;
      renderCollection(collection);
      scheduleSave();
    });
    row.append(clear);
  }
  wrapper.append(row);
  return wrapper;
}

async function lookupCanonical(collection, entity, wrapper) {
  wrapper.querySelector(".lookup-results")?.remove();
  const query = entity.name || entity.vendor || entity.original_extracted_text || "";
  if (!query.trim()) {
    showNotice("Enter a name or original extracted value before canonical lookup.");
    return;
  }
  const params = new URLSearchParams({ query, entity_type: canonicalType(collection, entity) });
  try {
    const response = await fetch(`/api/v1/canonical-entities?${params}`);
    if (!response.ok) throw new Error(await responseMessage(response));
    const payload = await response.json();
    const results = document.createElement("div");
    results.className = "lookup-results";
    if (!payload.candidates.length) results.textContent = "No canonical matches.";
    payload.candidates.forEach((candidate) => {
      const button = document.createElement("button");
      button.type = "button";
      button.textContent = `${candidate.label} · ${candidate.canonical_id}`;
      button.addEventListener("click", () => {
        entity.canonical_reference = candidate;
        renderCollection(collection);
        scheduleSave();
      });
      results.append(button);
    });
    wrapper.append(results);
  } catch (error) {
    showNotice(error.message || "Canonical lookup failed.");
  }
}

function addEntity(event) {
  const collection = event.currentTarget.closest(".entity-section").dataset.collection;
  state.draft[collection].push(defaultEntity(collection));
  renderCollection(collection);
  scheduleSave();
}

function deleteEntity(collection, entityId) {
  state.draft[collection] = state.draft[collection].filter((entity) => entity.entity_id !== entityId);
  state.draft.relationships = state.draft.relationships.filter(
    (relationship) => relationship.subject_id !== entityId && relationship.object_id !== entityId,
  );
  renderCollection(collection);
  renderRelationships();
  scheduleSave();
}

function updateEntityField(collection, entityId, field, rawValue) {
  const entity = state.draft[collection].find((item) => item.entity_id === entityId);
  if (!entity) return;
  let value = optionalText(rawValue);
  if (field.type === "number" && value !== null) value = field.integer ? Number.parseInt(value, 10) : Number(value);
  setNestedValue(entity, field.key, value);
  cleanupQuantity(entity, field.key.split(".")[0]);
  if (field.key === "role" || field.key === "context_type") renderRelationships();
  scheduleSave();
}

function renderRelationships() {
  const container = document.querySelector("#relationship-list");
  container.replaceChildren();
  if (!state.draft.relationships.length) {
    container.append(emptyRow("No scientific relationships recorded."));
    return;
  }
  state.draft.relationships.forEach((relationship, index) => {
    const card = document.createElement("article");
    card.className = "entity-card relationship-card";
    const header = document.createElement("div");
    header.className = "entity-card-header";
    const title = document.createElement("strong");
    title.textContent = `Relationship ${index + 1}`;
    const remove = document.createElement("button");
    remove.className = "delete-button";
    remove.type = "button";
    remove.textContent = "Remove";
    remove.addEventListener("click", () => {
      state.draft.relationships = state.draft.relationships.filter((item) => item.relationship_id !== relationship.relationship_id);
      renderRelationships();
      scheduleSave();
    });
    header.append(title, remove);
    const grid = document.createElement("div");
    grid.className = "field-grid";
    grid.append(relationshipSelect(relationship, "relation_type", Object.keys(RELATIONSHIPS)));
    const [subjectFilter, objectFilter] = RELATIONSHIPS[relationship.relation_type];
    grid.append(relationshipEntitySelect(relationship, "subject_id", subjectFilter));
    grid.append(relationshipEntitySelect(relationship, "object_id", objectFilter));
    card.append(header, grid);
    container.append(card);
  });
}

function relationshipSelect(relationship, key, options) {
  const label = document.createElement("label");
  label.append(document.createTextNode("Relationship"));
  const select = document.createElement("select");
  options.forEach((value) => select.append(new Option(humanize(value), value)));
  select.value = relationship[key];
  select.addEventListener("change", () => {
    relationship[key] = select.value;
    const [subjects, objects] = compatibleEntities(select.value);
    relationship.subject_id = subjects[0]?.entity.entity_id || "";
    relationship.object_id = objects[0]?.entity.entity_id || "";
    renderRelationships();
    scheduleSave();
  });
  label.append(select);
  return label;
}

function relationshipEntitySelect(relationship, key, predicate) {
  const label = document.createElement("label");
  label.append(document.createTextNode(key === "subject_id" ? "Subject" : "Object"));
  const select = document.createElement("select");
  entityEntries(state.draft).filter(({ entity, collection }) => predicate(entity, collection)).forEach(({ entity, collection }) => {
    select.append(new Option(entityLabel(entity, collection), entity.entity_id));
  });
  select.value = relationship[key];
  select.addEventListener("change", () => {
    relationship[key] = select.value;
    scheduleSave();
  });
  label.append(select);
  return label;
}

function addRelationship() {
  for (const relationType of Object.keys(RELATIONSHIPS)) {
    const [subjects, objects] = compatibleEntities(relationType);
    if (subjects.length && objects.length) {
      state.draft.relationships.push({
        schema_version: "1.0",
        relationship_id: crypto.randomUUID(),
        subject_id: subjects[0].entity.entity_id,
        relation_type: relationType,
        object_id: objects[0].entity.entity_id,
      });
      renderRelationships();
      scheduleSave();
      return;
    }
  }
  showNotice("Add compatible lane, protein, condition, or measurement entities first.");
}

function compatibleEntities(relationType) {
  const [subjectFilter, objectFilter] = RELATIONSHIPS[relationType];
  const entries = entityEntries(state.draft);
  return [
    entries.filter(({ entity, collection }) => subjectFilter(entity, collection)),
    entries.filter(({ entity, collection }) => objectFilter(entity, collection)),
  ];
}

function renderErrorCodes() {
  const container = document.querySelector("#error-codes");
  container.replaceChildren();
  const selected = new Set(state.editor.head_revision?.error_codes || []);
  state.editor.error_codes.forEach((record) => {
    const label = document.createElement("label");
    const input = document.createElement("input");
    input.type = "checkbox";
    input.value = record.code;
    input.checked = selected.has(record.code);
    input.addEventListener("change", () => scheduleSave());
    const name = document.createElement("span");
    name.textContent = humanize(record.code);
    const description = document.createElement("small");
    description.textContent = record.description;
    label.append(input, name, description);
    container.append(label);
  });
}

function selectedErrorCodes() {
  return [...document.querySelectorAll("#error-codes input:checked")].map((input) => input.value);
}

function renderDiff() {
  const summary = document.querySelector("#diff-summary");
  const list = document.querySelector("#diff-list");
  summary.replaceChildren();
  list.replaceChildren();
  const comparison = state.editor.comparison;
  if (!comparison) {
    list.append(emptyRow("No compatible prediction selected."));
    return;
  }
  ["modified", "added", "removed", "unchanged"].forEach((status) => {
    const chip = document.createElement("span");
    chip.className = `diff-chip ${status}`;
    chip.textContent = `${comparison[`${status}_count`]} ${status}`;
    summary.append(chip);
  });
  comparison.differences.filter((difference) => difference.status !== "unchanged").slice(0, 80).forEach((difference) => {
    const row = document.createElement("div");
    row.className = `diff-row ${difference.status}`;
    const path = document.createElement("code");
    path.textContent = difference.field_path;
    const value = document.createElement("span");
    value.textContent = `${humanize(difference.status)} · model ${difference.prediction_value_json ?? "∅"} · review ${difference.annotation_value_json ?? "∅"}`;
    row.append(path, value);
    list.append(row);
  });
  if (!list.children.length) list.append(emptyRow("Annotation matches the selected prediction."));
}

function renderRevisions() {
  const container = document.querySelector("#revision-list");
  container.replaceChildren();
  if (!state.editor.revisions.length) {
    container.append(emptyRow("No committed revisions yet."));
    return;
  }
  [...state.editor.revisions].reverse().forEach((revision) => {
    const card = document.createElement("div");
    card.className = "revision-card";
    const title = document.createElement("strong");
    title.textContent = `Revision ${revision.revision_number}`;
    const detail = document.createElement("span");
    detail.textContent = `${formatDate(revision.created_at)}${revision.rationale ? ` · ${revision.rationale}` : ""}`;
    card.append(title, detail);
    if (revision.revision_id !== state.headRevisionId && revision.has_structured_annotation) {
      const undo = document.createElement("button");
      undo.type = "button";
      undo.textContent = "Restore";
      undo.addEventListener("click", () => undoRevision(revision));
      card.append(undo);
    }
    container.append(card);
  });
}

async function acceptPrediction(entityIds, acceptAll) {
  if (!state.selectedPredictionId) return;
  try {
    const response = await fetch(
      `/api/v1/evaluation-cases/${caseId}/structured-annotation-drafts/accept-prediction`,
      {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          schema_version: "1.0",
          prediction_id: state.selectedPredictionId,
          base_annotation: state.draft,
          accepted_entity_ids: entityIds,
          accept_all: acceptAll,
        }),
      },
    );
    if (!response.ok) throw new Error(await responseMessage(response));
    const payload = await response.json();
    state.draft = payload.annotation;
    state.editor.comparison = payload.comparison;
    reviewerNotes.value = state.draft.reviewer_notes || "";
    COLLECTIONS.forEach(renderCollection);
    renderRelationships();
    renderDiff();
    rawJson.value = JSON.stringify(state.draft, null, 2);
    scheduleSave(0);
  } catch (error) {
    showNotice(error.message || "Prediction acceptance failed.");
  }
}

async function changePrediction() {
  if (state.mutationVersion !== state.savedVersion) await saveNow();
  if (state.mutationVersion !== state.savedVersion) {
    predictionSelect.value = state.selectedPredictionId || "";
    return;
  }
  await loadEditor(predictionSelect.value || null);
}

function scheduleSave(delay = 900) {
  state.mutationVersion += 1;
  window.clearTimeout(state.saveTimer);
  state.saveTimer = window.setTimeout(saveNow, delay);
  setSaveStatus("Unsaved changes", "saving");
}

async function saveNow() {
  window.clearTimeout(state.saveTimer);
  if (state.saving || state.mutationVersion === state.savedVersion || !state.draft) return;
  state.saving = true;
  const version = state.mutationVersion;
  const snapshot = deepCopy(state.draft);
  let saved = false;
  setSaveStatus("Saving revision…", "saving");
  hideNotice();
  try {
    const response = await fetch(`/api/v1/evaluation-cases/${caseId}/structured-annotations`, {
      method: "PUT",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        schema_version: "1.0",
        reviewer_id: reviewerId,
        expected_head_revision_id: state.headRevisionId,
        annotation: snapshot,
        rationale: optionalText(rationale.value),
        error_codes: selectedErrorCodes(),
      }),
    });
    if (!response.ok) throw new Error(await responseMessage(response));
    const payload = await response.json();
    state.documentId = payload.annotation.annotation_id;
    state.headRevisionId = payload.revision.revision_id;
    state.savedVersion = version;
    saved = true;
    setSaveStatus(`Saved revision ${payload.revision.revision_number}`);
    if (state.mutationVersion === version) await refreshMetadata();
  } catch (error) {
    showNotice(error.message || "Autosave failed. Your draft remains in this browser.");
    setSaveStatus("Save failed", "error");
  } finally {
    state.saving = false;
    if (saved && state.mutationVersion !== state.savedVersion) {
      state.saveTimer = window.setTimeout(saveNow, 900);
    }
  }
}

async function refreshMetadata() {
  const params = new URLSearchParams({ reviewer_id: reviewerId });
  if (state.selectedPredictionId) params.set("prediction_id", state.selectedPredictionId);
  const response = await fetch(`/api/v1/evaluation-cases/${caseId}/structured-editor?${params}`);
  if (!response.ok) return;
  const refreshed = await response.json();
  if (state.mutationVersion !== state.savedVersion) return;
  state.editor = refreshed;
  state.draft = deepCopy(refreshed.draft_annotation);
  state.documentId = refreshed.annotation_document?.annotation_id || null;
  state.headRevisionId = refreshed.head_revision?.revision_id || null;
  renderDiff();
  renderRevisions();
  rawJson.value = JSON.stringify(state.draft, null, 2);
}

async function undoRevision(revision) {
  if (!state.documentId || !state.headRevisionId) return;
  if (!window.confirm(`Restore revision ${revision.revision_number} as a new revision?`)) return;
  try {
    const response = await fetch(`/api/v1/annotations/${state.documentId}/structured-undo`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        schema_version: "1.0",
        reviewer_id: reviewerId,
        expected_head_revision_id: state.headRevisionId,
        target_revision_id: revision.revision_id,
        rationale: `Restored revision ${revision.revision_number}`,
      }),
    });
    if (!response.ok) throw new Error(await responseMessage(response));
    state.mutationVersion += 1;
    state.savedVersion = state.mutationVersion;
    await loadEditor(state.selectedPredictionId);
  } catch (error) {
    showNotice(error.message || "Revision restore failed.");
  }
}

function applyRawJson() {
  try {
    const parsed = JSON.parse(rawJson.value);
    state.draft = parsed;
    reviewerNotes.value = parsed.reviewer_notes || "";
    COLLECTIONS.forEach(renderCollection);
    renderRelationships();
    scheduleSave(0);
  } catch (error) {
    showNotice(`JSON could not be parsed: ${error.message}`);
  }
}

function defaultEntity(collection) {
  const base = {
    schema_version: "1.0",
    entity_id: crypto.randomUUID(),
    state: collection === "lane_conditions" ? "present" : "unknown",
    original_extracted_text: null,
    evidence_region_ids: [],
    notes: null,
  };
  if (collection === "proteins") return { ...base, entity_type: "protein", role: "target", name: null, canonical_reference: null };
  if (collection === "biological_contexts") return { ...base, entity_type: "biological_context", context_type: "cell_line", name: null, canonical_reference: null };
  if (collection === "treatments") return { ...base, entity_type: "treatment", name: null, dose: null, duration: null, canonical_reference: null };
  if (collection === "lane_conditions") return { ...base, entity_type: "lane_condition", lane_index: nextLaneIndex(), lane_label: null, condition_label: null };
  if (collection === "antibodies") return { ...base, entity_type: "antibody", name: null, vendor: null, catalog_number: null, clone: null, host_species: null, dilution: null, canonical_reference: null };
  if (collection === "molecular_weights") return { ...base, entity_type: "molecular_weight", value_kda: null };
  return { ...base, entity_type: "replicate", biological_replicates: null, technical_replicates: null, description: null };
}

function nextLaneIndex() {
  return Math.max(0, ...state.draft.lane_conditions.map((lane) => lane.lane_index || 0)) + 1;
}

function entityEntries(annotation) {
  return COLLECTIONS.flatMap((collection) => (annotation[collection] || []).map((entity) => ({ entity, collection })));
}

function entityLabel(entity, collection) {
  return entity.name || entity.lane_label || entity.condition_label || entity.description ||
    (entity.value_kda ? `${entity.value_kda} kDa` : null) ||
    (entity.biological_replicates ? `${entity.biological_replicates} biological replicates` : null) ||
    `${DEFINITIONS[collection].title} ${shortId(entity.entity_id)}`;
}

function supportsCanonicalLookup(collection) {
  return ["proteins", "biological_contexts", "treatments", "antibodies"].includes(collection);
}

function canonicalType(collection, entity) {
  if (collection === "proteins") return "protein";
  if (collection === "biological_contexts") return entity.context_type;
  if (collection === "treatments") return "treatment";
  return "antibody";
}

function isLane(_, collection) { return collection === "lane_conditions"; }
function isProtein(_, collection) { return collection === "proteins"; }
function textField(key, label, span = false, multiline = false) { return { key, label, type: "text", span, multiline }; }
function numberField(key, label, span = false, integer = false) { return { key, label, type: "number", span, integer }; }
function selectField(key, label, options) { return { key, label, type: "select", options }; }

function nestedValue(object, path) {
  return path.split(".").reduce((value, key) => value?.[key], object);
}

function setNestedValue(object, path, value) {
  const [parent, child] = path.split(".");
  if (!child) {
    object[parent] = value;
    return;
  }
  if (!object[parent]) object[parent] = { schema_version: "1.0", value: null, unit: null };
  object[parent][child] = value;
}

function cleanupQuantity(entity, parent) {
  if (!["dose", "duration"].includes(parent) || !entity[parent]) return;
  if (entity[parent].value == null && !entity[parent].unit) entity[parent] = null;
}

function emptyRow(message) {
  const row = document.createElement("div");
  row.className = "empty-row";
  row.textContent = message;
  return row;
}

function showNotice(message) { notice.textContent = message; notice.hidden = false; }
function hideNotice() { notice.hidden = true; notice.textContent = ""; }
function setSaveStatus(message, variant = "") { saveStatus.textContent = message; saveStatus.className = `save-status ${variant}`.trim(); }
function optionalText(value) { const trimmed = String(value ?? "").trim(); return trimmed || null; }
function deepCopy(value) { return JSON.parse(JSON.stringify(value)); }
function shortId(value) { return String(value).slice(0, 8); }
function humanize(value) { return String(value).replaceAll("_", " ").replace(/\b\w/g, (letter) => letter.toUpperCase()); }
function formatDate(value) { return new Intl.DateTimeFormat(undefined, { dateStyle: "medium", timeStyle: "short" }).format(new Date(value)); }

function localReviewerId() {
  const key = "hiveblot.localReviewerId";
  let value = window.localStorage.getItem(key);
  if (!value) {
    value = crypto.randomUUID();
    window.localStorage.setItem(key, value);
  }
  return value;
}

async function responseMessage(response) {
  try {
    const payload = await response.json();
    if (typeof payload.detail === "string") return payload.detail;
    if (Array.isArray(payload.detail)) return payload.detail.map((item) => `${item.loc?.join(".")}: ${item.msg}`).join("\n");
    return `Request failed (${response.status}).`;
  } catch (_) {
    return `Request failed (${response.status}).`;
  }
}
