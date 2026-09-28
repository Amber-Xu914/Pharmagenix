import { api } from "./api.js";
import { renderAvailability } from "./availability.js";
import { NetworkView } from "./network.js";
import { renderReports } from "./summary.js";
import { DgiTable } from "./table.js";
import { categoryColor, el } from "./util.js";

const $ = (id) => document.getElementById(id);
const state = { meta: null, query: null };

const table = new DgiTable({
  table: $("dgi-table"),
  filterInput: $("table-filter"),
  categorySelect: $("table-category"),
  countEl: $("table-count"),
  exportButton: $("export-csv"),
});
const network = new NetworkView({ container: $("network"), info: $("network-info"), legend: $("network-legend") });

const SOURCE_NAMES = {
  dgidb: "DGIdb", chembl: "ChEMBL", cpic: "CPIC", pharmgkb: "PharmGKB",
  pubtator: "PubTator", drugbank: "DrugBank", pbs: "PBS",
};
const SOURCE_STATES = { ok: "✓", error: "✗ failed", skipped: "not requested", disabled: "not enabled" };

async function init() {
  try {
    state.meta = await api.meta();
  } catch (err) {
    showStatus(`Cannot reach the backend: ${err.message}`, "error");
    return;
  }
  renderRelationChips();
  const comparison = state.meta.providers.comparison;
  $("compare-label").textContent = `Generate comparison report (with Claude)${comparison.enabled ? "" : " — API key not configured"}`;

  const queryId = new URLSearchParams(location.search).get("q");
  if (queryId) {
    try {
      showQuery(await api.getQuery(queryId));
    } catch (err) {
      showStatus(`Could not load query #${queryId}: ${err.message}`, "error");
    }
  }
}

function renderRelationChips() {
  const selected = new Set(state.meta.default_relation_types);
  $("relation-types").replaceChildren(...state.meta.relation_types.map(({ key, label }) =>
    el("label", { class: "chip" },
      el("input", { type: "checkbox", name: "relation", value: key, checked: selected.has(key) }),
      el("span", { class: "dot", style: `background:${categoryColor(key)}` }),
      label)));
}

function selectedRelations() {
  return [...document.querySelectorAll('input[name="relation"]:checked')].map((i) => i.value);
}

function showStatus(message, kind = "info", busy = false) {
  const box = $("status");
  box.className = `status ${kind === "error" ? "error" : ""}`;
  box.replaceChildren(busy ? el("span", { class: "spinner" }) : "", message);
}

function hideStatus() {
  $("status").className = "status hidden";
}

async function withBusy(buttons, message, task) {
  buttons.forEach((b) => { b.disabled = true; });
  showStatus(message, "info", true);
  try {
    const query = await task();
    hideStatus();
    showQuery(query);
  } catch (err) {
    showStatus(`Request failed: ${err.message}`, "error");
  } finally {
    buttons.forEach((b) => { b.disabled = false; });
  }
}

function showQuery(query) {
  state.query = query;
  history.replaceState(null, "", `?q=${query.id}`);
  $("results").classList.remove("hidden");

  const status = query.evidence?.source_status || {};
  $("source-status").replaceChildren(
    el("span", { class: "muted small" }, `Query #${query.id}${query.parent_id ? ` (follow-up to #${query.parent_id})` : ""}:`),
    ...Object.entries(SOURCE_NAMES).map(([key, name]) => {
      const s = status[key] || { state: "skipped" };
      const label = s.state === "ok" ? `${name} ✓ ${s.count}` : `${name} ${SOURCE_STATES[s.state] || s.state}`;
      return el("span", { class: `source-pill ${s.state}`, title: s.error || s.version || "" }, label);
    }));
  renderNormalisation(query.evidence?.normalisation || []);

  renderReports($("reports"), query);
  table.setRows(query.evidence?.table || []);
  network.render(query.evidence?.network, Number($("edge-limit").value));
  renderAvailability($("availability"), query.evidence?.australian_availability);
}

/** Show how each input was interpreted, e.g. "P53 → TP53 (HGNC:11998, HGNC previous symbol)". */
function renderNormalisation(entries) {
  const box = $("normalisation");
  const shown = entries.filter((e) => e.type === null || e.name !== e.input.trim().toUpperCase() || e.note);
  box.classList.toggle("hidden", !shown.length);
  box.replaceChildren(el("strong", {}, "Input interpreted as: "), ...shown.flatMap((e, i) => [
    i ? " · " : "",
    e.type
      ? `${e.input} → ${e.name} (${[e.id, e.matched_by].filter(Boolean).join(", ")})`
      : el("span", { class: "report-warning" }, `${e.input}: ${e.note}`),
  ]));
}

$("search-form").addEventListener("submit", (event) => {
  event.preventDefault();
  const body = {
    entities: $("entities").value,
    entity_type: $("entity-type").value,
    relation_types: selectedRelations(),
    compare: $("compare").checked,
  };
  withBusy([$("submit-btn")], "Fetching data from DGIdb / PharmGKB / PubTator / DrugBank and generating the report. This usually takes 20–60 seconds…",
    () => api.explore(body));
});

$("ask-form").addEventListener("submit", (event) => {
  event.preventDefault();
  if (!state.query) return;
  const body = { followup_question: $("followup").value, compare: $("ask-compare").checked };
  withBusy([...event.target.querySelectorAll("button")], "Regenerating the report from the existing evidence…",
    () => api.ask(state.query.id, body));
});

$("panel-file").addEventListener("change", async (event) => {
  const [file] = event.target.files;
  if (!file) return;
  try {
    const result = await api.parsePanel(file);
    $("entities").value = result.genes.join(", ");
    $("entity-type").value = "gene";
    const notes = [`Read ${result.genes.length} genes`];
    if (result.truncated) notes.push(`truncated to the limit of ${state.meta.limits.max_entities}`);
    if (result.ignored.length) notes.push(`ignored: ${result.ignored.slice(0, 5).join(", ")}`);
    $("panel-info").textContent = notes.join("; ");
  } catch (err) {
    $("panel-info").textContent = `Could not parse the file: ${err.message}`;
  } finally {
    event.target.value = "";
  }
});

$("edge-limit").addEventListener("change", () => {
  if (state.query) network.render(state.query.evidence?.network, Number($("edge-limit").value));
});
$("network-fit").addEventListener("click", () => network.fit());

document.addEventListener("evidence:focus", (event) => {
  table.highlight(event.detail);
  network.focusEvidence(event.detail);
});

init();
