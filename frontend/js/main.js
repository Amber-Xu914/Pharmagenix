import { api } from "./api.js";
import { renderAvailability } from "./availability.js";
import { NetworkView } from "./network.js";
import {
  headlineNumbers, interactionRows, pgxAlerts, queryGene, renderAlerts, renderHeader, renderLiterature, renderPgx,
} from "./profile.js";
import { renderReports } from "./summary.js";
import { DgiTable } from "./table.js";
import { categoryColor, el } from "./util.js";
import { initVcf, renderVcfQuery, vcfEvidenceTarget } from "./vcf.js";
import { renderProfiles, renderResolution, renderTrace, renderVerdict } from "./workflow.js";

const $ = (id) => document.getElementById(id);
const state = { meta: null, query: null, view: "table", networkStale: true };

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

  initVcf({ run: (task, message) => withBusy([$("vcf-submit")], message, task) });

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
  $("resolution").classList.add("hidden");
  showStatus(message, "info", true);
  try {
    const query = await task();
    hideStatus();
    showQuery(query);
  } catch (err) {
    if (err.data?.code === "needs_confirmation") {
      hideStatus();
      askToConfirm(err.data.resolution);
    } else {
      showStatus(`Request failed: ${err.message}`, "error");
    }
  } finally {
    buttons.forEach((b) => { b.disabled = false; });
  }
}

/** Render a query result. hash: a section to scroll to (from a shared link such as ?q=2#sec-pgx). */
function showQuery(query, { hash = "" } = {}) {
  state.query = query;
  const evidence = query.evidence || {};
  const section = /^#(vcf-)?sec-/.test(hash) ? document.querySelector(hash) : null;
  history.replaceState(null, "", `?q=${query.id}${section ? hash : ""}`);
  const vcf = query.entity_type === "vcf";
  $("results").classList.toggle("hidden", vcf);
  $("vcf-results").classList.toggle("hidden", !vcf);
  if (vcf) {
    renderVcfQuery(query);
    (section || $("vcf-results")).scrollIntoView({ behavior: scrollBehavior(), block: "start" });
    return;
  }

  const alerts = pgxAlerts(evidence.items || []);
  renderTrace($("workflow-trace"), query);
  renderVerdict($("verdict"), query);
  renderReports($("reports"), query, { alerts: () => renderAlerts(alerts) });

  const numbers = headlineNumbers(evidence);
  renderHeader($("entity-header"), { query, gene: queryGene(query), numbers });
  document.querySelectorAll("#section-nav [data-count]").forEach((span) => {
    span.textContent = numbers.counts[span.dataset.count] ?? "";
  });

  const items = evidence.items || [];
  table.setRows(interactionRows(evidence.table || []), {
    items: new Map(items.map((i) => [i.id, i])),
    availability: evidence.australian_availability,
  });
  state.networkStale = true;
  if (state.view === "network") renderNetwork();

  renderProfiles($("profiles"), items);
  const relations = query.relation_types || [];
  renderPgx($("pgx"), items, { requested: !relations.length || relations.includes("snp") });
  renderLiterature($("literature"), items);
  renderAvailability($("availability"), evidence.australian_availability);

  (section || $("results")).scrollIntoView({ behavior: scrollBehavior(), block: "start" });
}

function renderNetwork() {
  network.render(state.query?.evidence?.network, Number($("edge-limit").value));
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
function revealEvidence(eid) {
  const item = state.query?.evidence?.items?.find((i) => i.id === eid);
  if (!item) return;
  let target = null;
  if (state.query.entity_type === "vcf") {
    target = vcfEvidenceTarget(eid);
  } else if (state.view === "network" && ["interaction", "mechanism"].includes(item.kind) && network.focusEvidence(eid)) {
    target = $("network");
  } else if (["interaction", "mechanism"].includes(item.kind)) {
    target = table.highlight(eid) || null;
  } else { // PGx rows, papers and the profile card are .ev-row elements
    target = document.querySelector(`#results .ev-row[data-eid="${CSS.escape(eid)}"]`);
  }
  if (!target) return;
  document.querySelectorAll("#results .highlight").forEach((n) => n.classList.remove("highlight"));
  if (target !== $("network")) target.classList.add("highlight");
  target.scrollIntoView({ behavior: scrollBehavior(), block: "center" });
}

/** Stage 1: the gene was unknown or ambiguous. Picking a suggestion puts it in the search box and searches again.
 *  "Search as typed" sends the name to the live sources unchanged. */
function askToConfirm(problems) {
  renderResolution($("resolution"), problems, {
    onPick(input, suggestion) {
      $("gene").value = suggestion.name;
      search();
    },
    onSearchAsTyped: () => search({ confirm_unresolved: true }),
  });
  $("resolution").scrollIntoView({ behavior: scrollBehavior(), block: "nearest" });
}

function search(extra = {}) {
  const body = {
    gene: $("gene").value,
    relation_types: selectedRelations(),
    compare: $("compare").checked,
    ...extra,
  };
  withBusy([$("submit-btn")], "Resolving the gene, then fetching its profile and drug interactions and generating the report. This usually takes 20–60 seconds…",
    () => api.explore(body));
}

$("search-form").addEventListener("submit", (event) => {
  event.preventDefault();
  search();
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
  $("gene").value = chip.dataset.gene;
  $("submit-btn").focus();
}));

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
