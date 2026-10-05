import { api } from "./api.js";
import { renderAvailability } from "./availability.js";
import { NetworkView } from "./network.js";
import {
  headlineNumbers, interactionRows, pgxAlerts, queryEntities, renderAlerts, renderHeader, renderLiterature, renderPgx,
  scopeEvidence,
} from "./profile.js";
import { renderReports } from "./summary.js";
import { DgiTable } from "./table.js";
import { categoryColor, el } from "./util.js";

const $ = (id) => document.getElementById(id);
// focus: the entity every section is narrowed to (null = all); scoped: the evidence for that focus.
const state = { meta: null, query: null, entities: [], focus: null, scoped: null, view: "table", networkStale: true };

const table = new DgiTable({
  table: $("dgi-table"),
  filterInput: $("table-filter"),
  categorySelect: $("table-category"),
  countEl: $("table-count"),
  exportButton: $("export-csv"),
  toggleButton: $("toggle-unspecified"),
});
const network = new NetworkView({ container: $("network"), info: $("network-info"), legend: $("network-legend") });
const scrollBehavior = () => (matchMedia("(prefers-reduced-motion: reduce)").matches ? "auto" : "smooth");

async function init() {
  try {
    state.meta = await api.meta();
  } catch (err) {
    showStatus(`Cannot reach the backend: ${err.message}`, "error");
    return;
  }
  renderRelationChips();
  renderDataVersion(state.meta.reference_data || []);
  const comparison = state.meta.providers.comparison;
  $("compare-label").textContent = `Generate comparison report (with Claude)${comparison.enabled ? "" : " — API key not configured"}`;

  const queryId = new URLSearchParams(location.search).get("q");
  if (queryId) {
    try {
      showQuery(await api.getQuery(queryId), { hash: location.hash });
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

/** "Reference data last updated …", like Open Targets' "Last update"; the full versions are in the tooltip. */
function renderDataVersion(releases) {
  const box = $("data-version");
  box.classList.toggle("hidden", !releases.length);
  if (!releases.length) return;
  const latest = releases.map((r) => r.imported_at).sort().at(-1);
  const date = new Date(latest).toLocaleDateString("en-AU", { day: "numeric", month: "long", year: "numeric" });
  box.replaceChildren("Reference data last updated: ", el("a", { href: "#about" }, date));
  box.title = releases.map((r) => `${r.source.toUpperCase()}: ${r.version}`).join("\n");
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

/** Render a query result. hash: a section to scroll to (from a shared link such as ?q=2#sec-pgx). */
function showQuery(query, { hash = "" } = {}) {
  state.query = query;
  state.entities = queryEntities(query);
  state.focus = null;
  const section = hash.startsWith("#sec-") ? document.querySelector(hash) : null;
  history.replaceState(null, "", `?q=${query.id}${section ? hash : ""}`);
  $("results").classList.remove("hidden");

  // The AI answer covers the whole query, so it isn't narrowed by the entity switcher.
  const alerts = pgxAlerts(query.evidence?.items || []);
  renderReports($("reports"), query, { alerts: () => renderAlerts(alerts) });
  renderScoped();

  (section || $("results")).scrollIntoView({ behavior: scrollBehavior(), block: "start" });
}

/** Everything below the AI card follows the entity switcher. */
function renderScoped() {
  const { query, entities, focus } = state;
  const scoped = scopeEvidence(query, focus);
  state.scoped = scoped;

  const numbers = headlineNumbers(scoped, entities, focus);
  renderHeader($("entity-header"), { query, entities, focus, numbers, onFocus: setFocus });
  document.querySelectorAll("#section-nav [data-count]").forEach((span) => {
    span.textContent = numbers.counts[span.dataset.count] ?? "";
  });

  table.setRows(interactionRows(scoped.table), {
    items: new Map(scoped.items.map((i) => [i.id, i])),
    availability: scoped.availability,
  });
  state.networkStale = true;
  if (state.view === "network") renderNetwork();

  const relations = query.relation_types || [];
  renderPgx($("pgx"), scoped.items, { requested: !relations.length || relations.includes("snp") });
  renderLiterature($("literature"), scoped.items);
  renderAvailability($("availability"), scoped.availability);
}

function setFocus(entity) {
  state.focus = entity;
  renderScoped();
}

function renderNetwork() {
  network.render(state.scoped?.network, Number($("edge-limit").value));
  state.networkStale = false;
}

function setView(view) {
  state.view = view;
  document.querySelectorAll("#sec-interactions [data-view]").forEach((b) => {
    b.classList.toggle("active", b.dataset.view === view);
    b.setAttribute("aria-selected", String(b.dataset.view === view));
  });
  document.querySelectorAll("#sec-interactions [data-pane]").forEach((pane) => {
    pane.classList.toggle("hidden", pane.dataset.pane !== view);
  });
  // Cytoscape can't lay out inside a hidden container, so the network is drawn when first shown.
  if (view === "network" && state.networkStale) renderNetwork();
}

/** Scroll to the row behind a citation (table, network, PGx or literature) and highlight it. */
function revealEvidence(eid, retried = false) {
  const item = state.query?.evidence?.items?.find((i) => i.id === eid);
  if (!item) return;
  let target = null;
  if (["interaction", "mechanism"].includes(item.kind)) {
    if (state.view === "network" && network.focusEvidence(eid)) target = $("network");
    else target = table.highlight(eid) || null;
  } else {
    target = document.querySelector(`#results .ev-row[data-eid="${CSS.escape(eid)}"]`);
  }
  if (!target && state.focus && !retried) {
    setFocus(null); // narrowed away by the entity switcher
    revealEvidence(eid, true);
    return;
  }
  if (!target) return;
  document.querySelectorAll("#results .highlight").forEach((n) => n.classList.remove("highlight"));
  if (target !== $("network")) target.classList.add("highlight");
  target.scrollIntoView({ behavior: scrollBehavior(), block: "center" });
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

// Example chips fill in the search box; the user still presses Explore (a query takes 20–60 s and calls the LLMs).
document.querySelectorAll(".example").forEach((chip) => chip.addEventListener("click", () => {
  $("entities").value = chip.dataset.entities;
  $("entity-type").value = chip.dataset.type;
  $("panel-info").textContent = "";
  $("submit-btn").focus();
}));

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
  if (state.query) renderNetwork();
});
$("network-fit").addEventListener("click", () => network.fit());
document.querySelectorAll("#sec-interactions [data-view]").forEach((b) =>
  b.addEventListener("click", () => setView(b.dataset.view)));

document.addEventListener("evidence:focus", (event) => revealEvidence(event.detail));

// Section nav: highlight the section currently in the middle of the screen.
const navLinks = [...document.querySelectorAll("#section-nav a")];
const spy = new IntersectionObserver((entries) => {
  for (const entry of entries) {
    if (!entry.isIntersecting) continue;
    navLinks.forEach((a) => a.classList.toggle("active", a.getAttribute("href") === `#${entry.target.id}`));
  }
}, { rootMargin: "-35% 0px -60% 0px" });
document.querySelectorAll("#results .section").forEach((section) => spy.observe(section));

init();
