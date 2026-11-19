import { api } from "./api.js";
import { renderAvailability } from "./availability.js";
import { renderGenotypes, renderGlance } from "./glance.js";
import { NetworkView } from "./network.js";
import {
  headlineNumbers, interactionRows, pgxAlerts, queryGene, renderAlerts, renderHeader, renderLiterature, renderPgx,
} from "./profile.js";
import { downloadReport } from "./report.js";
import { medicationList, renderSafety } from "./safety.js";
import { renderReports } from "./summary.js";
import { initTabs, revealTab, setTab } from "./tabs.js";
import { DgiTable } from "./table.js";
import { categoryColor, copyLink, el, labelTableCells } from "./util.js";
import { initVcf, renderVcfQuery, vcfEvidenceTarget } from "./vcf.js";
import { cacheNotice, renderProfiles, renderResolution, renderTrace, renderVerdict } from "./workflow.js";

const $ = (id) => document.getElementById(id);
const state = { meta: null, query: null, view: "table", networkStale: true, entityType: "gene", busy: false };

const SEARCH_MODES = {
  gene: { label: "Gene name", placeholder: "Search a gene, e.g. CYP2C19, BRAF or TPMT",
    busy: "Resolving the gene, then fetching its profile and drug interactions and generating the report." },
  drug: { label: "Drug name", placeholder: "Search a drug (generic or brand), e.g. clopidogrel, Plavix or codeine",
    busy: "Resolving the drug, then fetching its profile and the genes it interacts with and generating the report." },
};

/** The gene and drug searches share one form: the mode decides the placeholder, examples and request. */
document.addEventListener("mode:change", ({ detail: mode }) => {
  if (!SEARCH_MODES[mode]) return;
  state.entityType = mode;
  $("search-label").textContent = SEARCH_MODES[mode].label;
  $("gene").placeholder = SEARCH_MODES[mode].placeholder;
  $("gene").value = "";
  document.querySelectorAll("[data-examples]").forEach((box) => box.classList.toggle("hidden", box.dataset.examples !== mode));
  $("resolution").classList.add("hidden");
});

const table = new DgiTable({
  table: $("dgi-table"),
  filterInput: $("table-filter"),
  categorySelect: $("table-category"),
  countEl: $("table-count"),
  exportButton: $("export-csv"),
  toggleButton: $("toggle-unspecified"),
  moreButton: $("table-more"),
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
  const comparison = state.meta.providers.comparison; // the second model, named from the backend config
  document.querySelectorAll(".compare-label").forEach((label) => {
    label.textContent = `Generate comparison report (with ${comparison.model})${comparison.enabled ? "" : " — API key not configured"}`;
  });

  initVcf({ run: (task, message) => withBusy(message, task) });
  $("report-docx").addEventListener("click", () =>
    downloadReport(state.query, network, $("report-docx"), $("report-docx-status")));
  $("export-csv-tab").addEventListener("click", () => table.exportCsv());
  $("copy-link").addEventListener("click", () => copyLink($("copy-link-status")));

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
  $("relation-types").addEventListener("change", relationSummary);
  relationSummary();
}

/** "(all 6)" / "(3 of 6)" next to the folded relationship types, so a narrowed search is not missed. */
function relationSummary() {
  const total = state.meta.relation_types.length;
  const n = selectedRelations().length;
  $("relation-summary").textContent = n === total ? `(all ${total})` : `(${n} of ${total} selected)`;
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

// Every button that starts a search or regenerates a report. Only one such request runs at a time: otherwise
// whichever finished last would be shown, not the one asked for last.
const BUSY_BUTTONS = "#submit-btn, #vcf-submit, #ask-form button[type=submit], #vcf-ask-form button[type=submit]";

async function withBusy(message, task) {
  if (state.busy) return;
  state.busy = true;
  const buttons = [...document.querySelectorAll(BUSY_BUTTONS)];
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
    state.busy = false;
    buttons.forEach((b) => { b.disabled = false; });
  }
}

/** Render a query result. hash: a section to scroll to (from a shared link such as ?q=2#sec-pgx). */
function showQuery(query, { hash = "" } = {}) {
  state.query = query;
  $("report-docx-status").textContent = "";
  const evidence = query.evidence || {};
  const section = /^#(vcf-)?sec-/.test(hash) ? document.querySelector(hash) : null;
  history.replaceState(null, "", `?q=${query.id}${section ? hash : ""}`);
  const vcf = query.entity_type === "vcf";
  const main = $(vcf ? "vcf-results" : "results");
  if (section) revealTab(section);
  else setTab(main, "clinical");
  $("results").classList.toggle("hidden", vcf);
  $("vcf-results").classList.toggle("hidden", !vcf);
  if (vcf) {
    renderVcfQuery(query);
    (section || $("vcf-results")).scrollIntoView({ behavior: scrollBehavior(), block: "start" });
    return;
  }

  const alerts = pgxAlerts(evidence.items || []);
  renderTrace($("workflow-trace"), query);
  renderGlance($("glance"), query);
  renderVerdict($("verdict"), query);
  $("verdict").prepend(cacheNotice(evidence));
  renderReports($("reports"), query, { roles: ["primary"], brief: "#sec-report" });
  renderReports($("reports-full"), query, { title: "Full AI report", alerts: () => renderAlerts(alerts) });

  const numbers = headlineNumbers(evidence, query.entity_type);
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
  if (state.view === "network" && $("results").dataset.tab === "extra") renderNetwork();

  $("profile-title").textContent = query.entity_type === "drug" ? "Drug profile" : "Gene profile";
  renderProfiles($("profiles"), items);
  const relations = query.relation_types || [];
  const nGenotype = renderGenotypes($("genotypes-result"), evidence);
  $("sec-genotype").classList.toggle("hidden", !evidence.genotypes);
  document.querySelector(".genotype-link").classList.toggle("hidden", !evidence.genotypes);
  document.querySelector('[data-count="genotype"]').textContent = nGenotype || "";
  renderPgx($("pgx"), items, { requested: !relations.length || relations.includes("snp") });
  renderLiterature($("literature"), items);
  renderAvailability($("availability"), evidence.australian_availability);
  const seen = new Set();
  const warned = (evidence.table || []).filter((r) => r.warnings?.length && !seen.has(r.drug) && seen.add(r.drug))
    .map((r) => ({ drug: r.drug, warnings: r.warnings }));
  if (evidence.safety?.drug_warnings?.length) warned.unshift({ drug: query.entities[0], warnings: evidence.safety.drug_warnings });
  const nSafety = renderSafety($("safety"), evidence, { warned, formHint: "add them under the search box to check interactions" });
  document.querySelector('[data-count="safety"]').textContent = nSafety + warned.length || "";

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
  } else if (state.view === "network" && ["interaction", "mechanism"].includes(item.kind)
             && (setTab($("results"), "extra"), network.focusEvidence(eid))) {
    target = $("network");
  } else if (["interaction", "mechanism", "fda_cyp"].includes(item.kind)) {
    target = table.highlight(eid) || null;
  } else { // PGx rows, papers and the profile card are .ev-row elements
    target = document.querySelector(`#results .ev-row[data-eid="${CSS.escape(eid)}"]`);
  }
  if (!target) { // nothing on the page shows this item (e.g. papers in the VCF view): open its source instead
    if (item.url) window.open(item.url, "_blank", "noopener");
    return;
  }
  revealTab(target); // the Clinical view cites rows that live under Extra
  for (let d = target.closest("details"); d; d = d.parentElement?.closest("details")) d.open = true; // folded sections
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
  const type = state.entityType;
  const body = {
    entity_type: type,
    [type]: $("gene").value,
    relation_types: selectedRelations(),
    compare: $("compare").checked,
    medications: medicationList($("medications")),
    genotypes: $("genotypes").value.split(/[;\n]+/).map((s) => s.trim()).filter(Boolean),
    ...extra,
  };
  withBusy(`${SEARCH_MODES[type].busy} This usually takes 20–60 seconds…`, () => api.explore(body));
}

$("search-form").addEventListener("submit", (event) => {
  event.preventDefault();
  search();
});

$("ask-form").addEventListener("submit", (event) => {
  event.preventDefault();
  if (!state.query) return;
  const body = { followup_question: $("followup").value, compare: $("ask-compare").checked };
  withBusy("Regenerating the report from the existing evidence…", () => api.ask(state.query.id, body));
});

// Example chips fill in the search box; the user still presses Explore (a query takes 20–60 s and calls the LLMs).
document.querySelectorAll(".example").forEach((chip) => chip.addEventListener("click", () => {
  $("gene").value = chip.dataset.name;
  $("submit-btn").focus();
}));

$("edge-limit").addEventListener("change", () => {
  if (state.query) renderNetwork();
});
$("network-fit").addEventListener("click", () => network.fit());
document.querySelectorAll("#sec-interactions [data-view]").forEach((b) =>
  b.addEventListener("click", () => setView(b.dataset.view)));

document.addEventListener("evidence:focus", (event) => revealEvidence(event.detail));

// Cytoscape can't lay out a hidden container: draw the network when the Extra tab is first shown.
document.addEventListener("tab:change", ({ detail }) => {
  if (detail.main.id === "results" && detail.tab === "extra" && state.view === "network" && state.networkStale) {
    renderNetwork();
  }
});

// Section nav: highlight the section currently in the middle of the screen.
const navLinks = [...document.querySelectorAll("#section-nav a")];
const spy = new IntersectionObserver((entries) => {
  for (const entry of entries) {
    if (!entry.isIntersecting) continue;
    navLinks.forEach((a) => a.classList.toggle("active", a.getAttribute("href") === `#${entry.target.id}`));
  }
}, { rootMargin: "-35% 0px -60% 0px" });
document.querySelectorAll("#results .section").forEach((section) => spy.observe(section));

labelTableCells();
initTabs();
init();
