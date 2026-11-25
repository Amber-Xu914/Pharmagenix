/**
 * Workflow 4: patient VCF + cancer type -> ranked candidate drugs (lung cancer prototype, synthetic test data only).
 * Backend: routes/vcf.py and services/variants/. QC, tiers, tracks and scores come from deterministic tools; the
 * Analysis agent explains them and the exploration agents work on the exploratory track only.
 */
import { api } from "./api.js";
import { restrictionList } from "./availability.js";
import { NetworkView } from "./network.js";
import { downloadReport } from "./report.js";
import { medicationList, renderSafety, safetyMarkers } from "./safety.js";
import { renderVcfGlance } from "./glance.js";
import { renderReports } from "./summary.js";
import { copyLink, el, focusEvidence, latestOnly } from "./util.js";
import { SOURCE_NAMES, cacheNotice, renderTrace, sourcesChecked } from "./workflow.js";

const $ = (id) => document.getElementById(id);

export const VCF_STAGES = [
  ["qc", "VCF QC tool"],
  ["resolution", "Entity resolution"],
  ["tiering", "Variant tiering tool"],
  ["retrieval", "DGI retrieval"],
  ["classification", "Evidence classification"],
  ["exploration", "Exploration agents"],
  ["analysis", "Analysis agent"],
  ["report", "Report"],
];
const AGENT_ROLES = ["literature", "biology", "hypothesis"];
const AGENT_LABELS = { literature: "Literature", biology: "Biological interpretation", hypothesis: "Hypotheses" };
const VCF_SOURCES = { civic: "CIViC", oncokb: "OncoKB", vep: "Ensembl VEP", medcpt: "MedCPT re-ranking",
  pubtator_abstracts: "PubTator abstracts", ...SOURCE_NAMES };
const SOURCE_STATES = { ok: "✓", error: "✗ failed", skipped: "not run", disabled: "not enabled" };
const DROP_STAGES = { qc: "QC", germline: "Germline", consequence: "Consequence" };

const network = new NetworkView({
  container: $("vcf-network"), info: $("vcf-network-info"), legend: $("vcf-network-legend"),
  categories: ["variant_of", "sensitive", "gene_link", "resistant"],
});
let options = null;
let currentQuery = null; // for the report download

function refLink(eid) {
  return el("a", { class: "ref", href: "#", onclick: (e) => { e.preventDefault(); focusEvidence(eid); } }, eid);
}

function refs(eids, max = 6) {
  return [eids.slice(0, max).map((eid, i) => [i ? ", " : "", refLink(eid)]), eids.length > max ? ` +${eids.length - max}` : ""];
}

/** PubTator papers ({pmid, item_id}) as direct PubMed links; the VCF view has no literature list to scroll to. */
function paperLinks(papers, max = 3) {
  return [papers.slice(0, max).map((p, i) => [i ? ", " : "", el("a", {
    href: `https://pubmed.ncbi.nlm.nih.gov/${p.pmid}/`, target: "_blank", rel: "noopener",
    title: [p.title, p.score != null ? `relevance ${p.score}` : ""].filter(Boolean).join(" · "),
  }, `PMID ${p.pmid}`)]), papers.length > max ? ` +${papers.length - max}` : ""];
}

/** "3 PMIDs", linked to a PubMed search for them. */
function pmidSearch(pmids) {
  return el("a", { href: `https://pubmed.ncbi.nlm.nih.gov/?term=${pmids.slice(0, 20).join("+OR+")}`,
    target: "_blank", rel: "noopener", title: pmids.slice(0, 20).join(", ") },
  `${pmids.length} PMID${pmids.length > 1 ? "s" : ""}`);
}

const pct = (x) => (x == null ? "–" : `${Math.round(x * 1000) / 10}%`);
const where = (v) => `chr${v.chrom}:${v.pos} ${v.ref}>${v.alt}`;

// ---------- The form ----------

/** run(task, message): the page's busy / error handling (main.js). */
export async function initVcf({ run }) {
  document.querySelectorAll(".mode-switch [data-mode]").forEach((b) => b.addEventListener("click", () => setMode(b.dataset.mode)));
  document.querySelectorAll(".vcf-source [data-source]").forEach((b) => b.addEventListener("click", () => setSource(b.dataset.source)));

  try {
    options = await api.vcfOptions();
  } catch {
    return; // the page reports an unreachable backend already
  }
  // A combobox: the OncoTree lung types are suggestions, but any text can be typed (the backend maps names,
  // codes and common aliases such as "lung adenocarcinoma" to OncoTree).
  $("vcf-cancer-list").replaceChildren(...options.cancer_types.map((t) => el("option", { value: typeLabel(t.code) })));
  $("vcf-cancer").value = typeLabel("LUAD");
  // Browsers filter a datalist by the text in the box, so on focus the current value moves to the placeholder (the
  // whole list shows) and comes back on blur if nothing new was typed.
  const cancerBox = $("vcf-cancer");
  const defaultPlaceholder = cancerBox.placeholder;
  cancerBox.addEventListener("focus", () => {
    if (!cancerBox.value) return;
    cancerBox.placeholder = cancerBox.value;
    cancerBox.value = "";
  });
  cancerBox.addEventListener("blur", () => {
    if (!cancerBox.value.trim() && cancerBox.placeholder !== defaultPlaceholder) cancerBox.value = cancerBox.placeholder;
    cancerBox.placeholder = defaultPlaceholder;
  });
  renderExamples(options.examples);
  // Logging in or out changes whether the examples are offered (administrators only): ask the backend again.
  // Logging out and in again quickly starts two requests: only the answer to the latest one is used.
  const optionsRequest = latestOnly();
  document.addEventListener("auth:change", async () => {
    const isLatest = optionsRequest();
    try {
      const { examples } = await api.vcfOptions();
      if (isLatest()) renderExamples(examples);
    } catch { /* keep the current form */ }
  });
  $("vcf-ready").classList.toggle("hidden", options.ready);
  $("vcf-ready").textContent = options.ready ? ""
    : "Reference data for this workflow is not imported yet. Run: flask --app run import-data oncotree intogen civic hotspots cbioportal";

  $("vcf-form").addEventListener("submit", (event) => {
    event.preventDefault();
    const compare = $("vcf-compare").checked;
    const cancerType = $("vcf-cancer").value.trim();
    if (!cancerType) return run(() => Promise.reject(new Error("Type or pick a cancer type / subtype")), "");
    const message = "Running QC, tiering every variant, retrieving and classifying drug evidence, then the exploration "
      + "and analysis agents. This usually takes 30–90 seconds…";
    if (source() === "upload") {
      const [file] = $("vcf-file").files;
      if (!file) return run(() => Promise.reject(new Error("Choose a VCF file first")), message);
      if (!$("vcf-test-data").checked) {
        return run(() => Promise.reject(new Error("Please confirm the file is synthetic test data, not real patient data")), message);
      }
      const form = new FormData();
      form.append("file", file);
      form.append("cancer_type", cancerType);
      form.append("test_data", "1");
      form.append("medications", medicationList($("vcf-medications")).join(", "));
      for (const [key, value] of Object.entries(clinicalContext())) form.append(`clinical_${key}`, value);
      if (compare) form.append("compare", "1");
      return run(() => api.vcfExplore(form), message);
    }
    const example = document.querySelector('input[name="vcf-example"]:checked')?.value;
    return run(() => api.vcfExploreExample({ example, cancer_type: cancerType, compare,
                                             medications: medicationList($("vcf-medications")),
                                             clinical: clinicalContext() }), message);
  });

  $("vcf-ask-form").addEventListener("submit", (event) => {
    event.preventDefault();
    const id = $("vcf-results").dataset.queryId;
    if (!id) return;
    const body = { followup_question: $("vcf-followup").value, compare: $("vcf-ask-compare").checked };
    run(() => api.ask(id, body), "Regenerating the analysis and agent reports from the existing evidence…");
  });
  $("vcf-network-fit").addEventListener("click", () => network.fit());
  $("vcf-report-docx").addEventListener("click", () => downloadReport(currentQuery, network, $("vcf-report-docx"),
    $("vcf-report-docx-status")));
  $("vcf-copy-link").addEventListener("click", () => copyLink($("vcf-copy-link-status")));
  // Cytoscape can't lay out a hidden container: draw the network when the Extra tab is shown.
  document.addEventListener("tab:change", ({ detail }) => {
    if (detail.main.id === "vcf-results" && detail.tab === "extra" && networkStale) drawNetwork();
  });
}

/** The optional clinical-context fields: {stage, treatment, age, ...} with the empty ones left out. */
function clinicalContext() {
  const out = {};
  document.querySelectorAll("[data-clinical]").forEach((input) => {
    if (input.value.trim()) out[input.dataset.clinical] = input.value.trim();
  });
  return out;
}

/** The Clinical summary agent's report (brief results, regimen options, monitoring), with the [GK] legend. */
function renderClinical(query) {
  const box = $("vcf-clinical");
  box.replaceChildren();
  if (!query.reports.some((r) => r.role === "clinical")) {
    if (query.evidence?.outcome?.status === "found") {
      box.append(el("p", { class: "muted small" }, "No Clinical summary was generated for this query (run before the "
        + "agent existed, or it failed). The Analysis agent's report is under ", el("a", { href: "#vcf-sec-analysis" }, "Extra"), "."));
    }
    return;
  }
  const clinical = query.evidence?.input?.clinical || {};
  box.append(el("p", { class: "agent-note" }, "Clinical summary (AI): regimen options and monitoring for the treating "
    + "physician to weigh. [En] cites the evidence; [GK] marks general clinical knowledge that is not in the retrieved "
    + "evidence and must be checked against current guidelines.",
    Object.keys(clinical).length ? el("span", { class: "muted" }, ` Clinical context: ${Object.entries(clinical)
      .map(([k, v]) => `${k.replace("_", " ")} ${v}`).join(", ")}.`) : ""));
  const card = el("div");
  box.append(card);
  renderReports(card, query, { roles: ["clinical"], title: "Clinical summary", empty: "Not generated." });
}

function setMode(mode) {
  document.querySelectorAll(".mode-switch [data-mode]").forEach((b) => {
    b.classList.toggle("active", b.dataset.mode === mode);
    b.setAttribute("aria-selected", String(b.dataset.mode === mode));
  });
  document.querySelectorAll("[data-mode-pane]").forEach((p) =>
    p.classList.toggle("hidden", !p.dataset.modePane.split(" ").includes(mode)));
  document.dispatchEvent(new CustomEvent("mode:change", { detail: mode })); // gene / drug share the search form
}

function typeLabel(code) {
  const t = options?.cancer_types.find((x) => x.code === code);
  return t ? `${t.name} (${t.code})` : code;
}

/** The synthetic mock VCFs. The backend lists them only for an administrator; everyone else sees the upload form
 *  alone, without the "Synthetic example / Upload VCF" switch. */
function renderExamples(examples) {
  const admin = examples.length > 0;
  $("vcf-examples").replaceChildren(...examples.map((x, i) => el("label", { class: "vcf-example" },
    el("input", { type: "radio", name: "vcf-example", value: x.id, checked: i === 0,
                  onchange: () => { $("vcf-cancer").value = typeLabel(x.cancer_type); } }),
    el("span", {}, el("span", { class: "code" }, x.cancer_type), ` ${x.label} `,
      el("a", { href: `/api/vcf/examples/${encodeURIComponent(x.id)}`, class: "small", title: "Download the mock VCF",
                onclick: (e) => e.stopPropagation() }, "VCF ↓")))));
  document.querySelector(".vcf-source").classList.toggle("hidden", !admin);
  setSource(admin ? "example" : "upload");
  if (admin) $("vcf-cancer").value = typeLabel(examples[0].cancer_type);
  $("vcf-intro").textContent = admin ? "Lung cancer prototype. Upload a somatic VCF or pick a synthetic example."
    : "Lung cancer prototype. Upload a somatic VCF.";
}

function source() {
  return document.querySelector(".vcf-source button.active")?.dataset.source || "upload";
}

function setSource(name) {
  document.querySelectorAll(".vcf-source [data-source]").forEach((b) => {
    b.classList.toggle("active", b.dataset.source === name);
    b.setAttribute("aria-selected", String(b.dataset.source === name));
  });
  document.querySelectorAll("[data-source-pane]").forEach((p) => p.classList.toggle("hidden", p.dataset.sourcePane !== name));
}

// ---------- The results ----------

/** The exploration stage also reports how the agents did (they run after the deterministic tools). */
function withAgentTrace(query) {
  const evidence = query.evidence || {};
  if (!evidence.trace) return query;
  const latest = new Map();
  for (const r of query.reports.filter((r) => AGENT_ROLES.includes(r.role))) latest.set(r.role, r);
  const trace = evidence.trace.map((s) => {
    if (s.stage !== "exploration" || !latest.size) return s;
    const agents = [...latest.values()].map((r) => `${AGENT_LABELS[r.role]} ${r.status === "success" ? "✓" : r.status}`);
    const flagged = [...latest.values()].some((r) => r.status !== "success");
    return { ...s, status: flagged ? "warning" : "done", detail: `${s.detail} · ${agents.join(" · ")}` };
  });
  return { ...query, evidence: { ...evidence, trace } };
}

export function renderVcfQuery(query) {
  const evidence = query.evidence || {};
  currentQuery = query;
  $("vcf-report-docx-status").textContent = "";
  $("vcf-results").dataset.queryId = query.id;
  setMode("vcf");
  renderHeader(query, evidence);
  renderTrace($("vcf-trace"), withAgentTrace(query), VCF_STAGES);
  $("vcf-trace").append(cacheNotice(evidence));
  renderVcfGlance($("vcf-glance"), evidence);
  renderVerdict(evidence);
  renderReports($("vcf-reports"), query, { title: "Analysis agent" });
  renderClinical(query);
  document.querySelector("#vcf-sec-analysis .ask").classList.toggle("hidden", evidence.outcome?.status !== "found");
  renderEstablished(evidence);
  renderExploratory(query, evidence);
  renderCoMutations(evidence);
  const candidates = [...(evidence.tracks?.established || []), ...(evidence.tracks?.exploratory || [])];
  const options = (evidence.tracks?.established || []).filter((c) => c.top && !c.strong_resistance).map((c) => c.drug);
  // Clinical view: warnings of the treatment options only (the full drug lists are in the Appendix).
  const warned = candidates.filter((c) => options.includes(c.drug) && c.safety?.warnings?.length)
    .map((c) => ({ drug: c.drug, warnings: c.safety.warnings }));
  const nSafety = renderSafety($("vcf-safety"), evidence, { warned, regimens: true, focus: options,
    formHint: "enter the patient's medications in the form to check every candidate" });
  $("vcf-count-safety").textContent = nSafety + warned.length || "";
  renderAppendix(evidence);
  renderLimits(evidence.limitations || [], evidence.parameters);
  renderIsm(evidence);
  const tracks = evidence.tracks || {};
  $("vcf-count-established").textContent = tracks.established?.length || "";
  $("vcf-count-exploratory").textContent = tracks.exploratory?.length || "";
  // Cytoscape needs a visible container: the network is drawn when the Extra tab is shown.
  networkStale = true;
  if ($("vcf-results").dataset.tab === "extra") requestAnimationFrame(drawNetwork);
}

let networkStale = true;
function drawNetwork() {
  network.render(currentQuery?.evidence?.network, 0);
  networkStale = false;
}

function renderHeader(query, evidence) {
  const input = evidence.input || {};
  const cancer = input.cancer_type || {};
  const counts = evidence.variants?.counts || {};
  const tracks = evidence.tracks || {};
  const status = evidence.source_status || {};
  const kpis = [
    [counts.total, "Variants", "in the VCF"],
    [counts.qc_passed, "Passed QC", "somatic, protein-altering"],
    [counts.tier1, "Tier 1", "subtype-specific"],
    [counts.tier2, "Tier 2", "hotspot / other cancers"],
    [counts.tier3, "Tier 3", "weighted score S"],
    [counts.low_priority, "Low priority", "S below threshold"],
    [(tracks.established || []).filter((c) => !c.strong_resistance).length, "Established drugs",
     `${tracks.exploratory?.length ?? 0} exploratory`],
  ];
  $("vcf-header").replaceChildren(
    el("div", { class: "eh-top" }, el("span", { class: "test-badge" }, "Synthetic test data · research / teaching use"),
      el("span", { class: "muted small" }, `Query #${query.id}${query.parent_id ? ` (follow-up to #${query.parent_id})` : ""}`)),
    el("div", { class: "eh-title" }, el("h2", {}, query.entities[0]), el("span", { class: "type-badge gene" }, "Patient VCF")),
    el("p", { class: "eh-name" }, `${cancer.name || "?"} (OncoTree ${cancer.code || "?"})`),
    el("p", { class: "eh-meta small" }, [input.format?.toUpperCase(), `annotation: ${input.annotation || "Ensembl VEP"}`,
      input.assembly, input.tumour_sample && `tumour ${input.tumour_sample}`, input.normal_sample && `normal ${input.normal_sample}`]
      .filter(Boolean).join(" · ")),
    el("div", { class: "kpis" }, kpis.map(([value, label, sub]) => el("div", { class: "kpi" },
      el("span", { class: "kpi-value" }, value ?? "–"), el("span", { class: "kpi-label" }, label), el("span", { class: "kpi-sub" }, sub)))),
    el("div", { class: "source-status" }, el("span", { class: "muted small" }, "Sources:"),
      Object.entries(status).map(([key, s]) => el("span", { class: `source-pill ${s.state}`, title: s.error || s.version || "" },
        s.state === "ok" ? `${VCF_SOURCES[key] || key} ✓ ${s.count}` : `${VCF_SOURCES[key] || key} ${SOURCE_STATES[s.state] || s.state}`))),
  );
}

function renderVerdict(evidence) {
  const outcome = evidence.outcome || {};
  const box = $("vcf-verdict");
  box.replaceChildren();
  if (outcome.status === "found") {
    if (outcome.no_targeted_therapy) box.append(el("p", { class: "no-tt" }, outcome.no_targeted_therapy));
    return;
  }
  box.append(el("div", { class: "verdict none" },
    el("p", { class: "verdict-title" }, "No variant passed QC and tiering"),
    el("p", {}, "No drug databases were queried and no AI text was generated. Every variant and the reason it was "
      + "filtered or set to low priority is in the Appendix."),
    ...sourcesChecked(outcome, VCF_SOURCES)));
}

function levelBadge(c) {
  const parts = [];
  if (c.best) parts.push(el("span", { class: `level-badge tier-${c.best.level <= "B" ? 1 : c.best.level === "C" ? 2 : 3}` }, `CIViC ${c.best.level}`));
  if (c.oncokb_level) parts.push(" ", el("span", { class: "level-badge tier-1" }, `OncoKB ${c.oncokb_level.replace("LEVEL_", "")}`));
  if (!parts.length && c.gene_links?.length) {
    parts.push(el("span", { class: "level-badge tier-3" }, [...new Set(c.gene_links.map((g) => g.source))].join(" / ")));
  }
  return parts;
}

const MARKETED_TITLE = "ChEMBL max phase 4: approved by at least one regulator (mostly FDA/EMA) for at least one "
  + "indication. Not necessarily for this cancer, and not a TGA check.";
const PBS_TITLE = "Matched by drug name on the current PBS Schedule. The restrictions are shown as published; "
  + "whether this patient meets them is not checked.";

function candidateTable(rows, availability, { clinical = false } = {}) {
  const pbs = new Map((availability || []).map((a) => [a.drug.toUpperCase(), a]));
  return el("div", { class: "table-wrap" }, el("table", { class: "cand-table" },
    el("thead", {}, el("tr", {}, ["#", "Drug", "Target variant(s)", "Evidence", "Score", "Rationale", "Marketed / PBS", "Refs"]
      .map((h) => el("th", {}, h)))),
    el("tbody", {}, rows.map((c) => {
      const listing = pbs.get(c.drug.split(" + ")[0].toUpperCase());
      return el("tr", { class: "ev-row", "data-eids": c.evidence_ids.join(" ") },
        el("td", { class: "rank" }, c.rank),
        el("td", { class: "drug" }, c.drug, c.mechanism ? el("div", { class: "muted small" }, c.mechanism) : "",
          el("div", {}, safetyMarkers(c.safety?.warnings, c.safety?.interactions).map((m) => [m, " "]))),
        el("td", {}, c.variants.join(", ") || "–"),
        el("td", {}, levelBadge(c), c.best ? el("div", { class: "muted small" },
          `${c.best.disease_match} · ${c.best.specificity} match · ${c.n_evidence} item${c.n_evidence === 1 ? "" : "s"}`) : ""),
        el("td", {}, el("span", { class: "score" }, c.score), clinical
          // Clinical view: the points behind the score are folded (the Appendix under Extra lists them open).
          ? el("details", { class: "score-why small" }, el("summary", {}, "How scored"), scoreParts(c))
          : scoreParts(c)),
        el("td", { class: "small" }, c.track_reason,
          (c.co_occurrence || []).map((n) => el("div", { class: "note-co" }, `↔ ${n}`)),
          (c.resistance_notes || []).slice(0, 3).map((n) => el("div", { class: "note-resist" }, `⚠ ${n}`)),
          (c.conflicts || []).map((n) => el("div", { class: "report-warning" }, `⚠ ${n}`)),
          (c.safety?.interactions || []).map((a) => el("div", { class: `note-ddi ${a.severity}` },
            `⚠ ${a.severity} interaction (${a.context}): ${a.drug} with ${a.other}, ${a.mechanism} `, refLink(a.evidence_id))),
          (c.literature || []).length ? el("div", { class: "muted" }, "Literature: ", paperLinks(c.literature)) : ""),
        el("td", { class: "small" }, c.approved ? el("span", { title: MARKETED_TITLE }, "Marketed (ChEMBL phase 4)")
          : el("span", { class: "muted" }, "–"),
          listing ? el("div", {}, listing.pbs_listed ? [el("span", { title: PBS_TITLE }, "PBS listed"),
            restrictionList(listing.restrictions)] : el("span", { class: "muted" }, "Not on PBS")) : ""),
        el("td", { class: "small" }, refs(c.evidence_ids, 4),
          c.pmids?.length ? el("div", { class: "muted" }, pmidSearch(c.pmids)) : ""));
    }))));
}

function scoreParts(c) {
  return el("ul", { class: "score-parts" }, (c.score_parts || []).map((p) => el("li", {}, `${p.points > 0 ? "+" : ""}${p.points} ${p.label}`)));
}

function renderEstablished(evidence) {
  const rows = (evidence.tracks?.established || []).filter((c) => c.top);
  const avoid = evidence.resistance_only || [];
  const box = $("vcf-established");
  box.replaceChildren(
    evidence.outcome?.no_targeted_therapy ? el("p", { class: "no-tt" }, evidence.outcome.no_targeted_therapy) : "",
    rows.length ? candidateTable(rows, evidence.australian_availability, { clinical: true })
      : el("p", { class: "muted" }, "No drug has level A/B (OncoKB 1–2) evidence for these variants in this cancer subtype."),
    avoid.length ? el("div", {},
      el("h3", { class: "sub-head" }, `Drugs to avoid: resistance evidence only (${avoid.length})`),
      el("ul", { class: "small" }, avoid.map((c) => el("li", { class: "ev-row", "data-eids": c.evidence_ids.join(" ") },
        el("strong", {}, c.drug), `: ${c.resistance_notes.slice(0, 2).join("; ")} `, refs(c.evidence_ids, 3))))) : "",
  );
}

function tierBadge(tier) {
  return el("span", { class: `tier-badge tier-${tier}` }, tier ? `Tier ${tier}` : "Low priority");
}

/** F, R, B and S for a Tier 3 (or low-priority) variant, with the weights used. */
function scoreTable(rows) {
  return el("div", { class: "table-wrap" }, el("table", {},
    el("thead", {}, el("tr", {}, ["Variant", "F functional impact", "R subtype relevance", "B biological evidence", "S", "Literature"]
      .map((h) => el("th", {}, h)))),
    el("tbody", {}, rows.map((v) => {
      const t = v.tier3;
      const bParts = Object.entries(t.B_parts).filter(([, p]) => p).map(([k, p]) => `${k.replace("_", " ")} +${p}`);
      return el("tr", { class: "ev-row", "data-eid": v.evidence_id || "" },
        el("td", {}, tierBadge(v.tier), " ", v.notation),
        el("td", {}, `${t.F} `, el("span", { class: "muted small" }, t.F_source)),
        el("td", {}, `${t.R} `, el("span", { class: "muted small" }, `${pct(t.R_frequency)} · ${t.R_source}`)),
        el("td", {}, `${t.B} `, el("span", { class: "muted small" }, bParts.join(", ") || "none")),
        el("td", {}, el("span", { class: "score" }, t.S), el("div", { class: "formula muted" },
          `${t.weights.F}·F + ${t.weights.R}·R + ${t.weights.B}·B`)),
        el("td", { class: "small" }, (v.literature || []).length ? paperLinks(v.literature) : el("span", { class: "muted" }, "–")));
    }))));
}

function renderExploratory(query, evidence) {
  const rows = (evidence.tracks?.exploratory || []).filter((c) => c.top);
  const tier3 = (evidence.variants?.tiered || []).filter((v) => v.tier === 3);
  $("vcf-exploratory").replaceChildren(
    el("h3", { class: "sub-head" }, `Exploratory drugs (top ${rows.length} of ${evidence.tracks?.exploratory?.length || 0})`),
    rows.length ? candidateTable(rows, evidence.australian_availability) : el("p", { class: "muted" }, "None."),
    el("p", { class: "muted small" }, "Ranked by evidence, Tier 3 score S and literature support. Predictions, not "
      + "evidence of response; the full list is in the Appendix."),
    el("h3", { class: "sub-head" }, `Tier 3 variants: weighted score S (${tier3.length})`),
    tier3.length ? scoreTable(tier3) : el("p", { class: "muted" }, "None."),
  );
  const agents = query.reports.filter((r) => AGENT_ROLES.includes(r.role));
  const box = $("vcf-agents");
  box.replaceChildren();
  if (!agents.length) {
    if (evidence.outcome?.status === "found") box.append(el("p", { class: "muted small" }, "The exploration agents did not run."));
    return;
  }
  box.append(el("p", { class: "agent-note" }, "Exploration agents (AI): they work on this exploratory track only and cannot "
    + "change QC, tiers or evidence levels. Statements cite evidence or are labelled as hypotheses."));
  const card = el("div");
  box.append(card);
  renderReports(card, query, { roles: AGENT_ROLES, title: "Exploration agents", tabLabel: (r) => AGENT_LABELS[r.role],
                               empty: "The exploration agents did not run." });
}

function renderCoMutations(evidence) {
  const rows = evidence.co_mutations || [];
  const patterns = evidence.co_mutation_patterns || [];
  $("vcf-comutations").replaceChildren(
    patterns.length ? el("p", { class: "small" }, "Co-mutation patterns in this sample: ", el("strong", {}, patterns.join("; "))) : "",
    rows.length ? el("ul", { class: "small" }, rows.map((v) => el("li", {}, tierBadge(v.tier), ` ${v.notation}: `,
      (v.pathways || []).length ? `${v.pathways.join(", ")} pathway; ` : "", v.tier_reason)))
      : el("p", { class: "muted" }, "None: every tiered variant has variant-level drug evidence."),
  );
}

function variantRow(v) {
  return el("tr", {},
    el("td", {}, tierBadge(v.tier)), el("td", {}, v.notation || `${v.gene || "?"} ${v.protein_change || ""}`),
    el("td", {}, where(v)), el("td", {}, v.depth != null ? `${v.alt_reads}/${v.depth} (${pct(v.vaf)})` : "–"),
    el("td", { class: "small" }, v.tier_reason || v.reason));
}

// Rows shown at a time in the variant tables; "Show more" adds another page (a whole-exome VCF has thousands).
const TABLE_PAGE = 100;

function filterableTable(rows, header, rowFn) {
  const filter = el("input", { type: "search", placeholder: "Filter…" });
  const body = el("tbody");
  const more = el("button", { type: "button", class: "secondary small hidden" });
  const count = el("p", { class: "muted small" });
  let limit = TABLE_PAGE;
  const draw = () => {
    const text = filter.value.trim().toLowerCase();
    const matching = rows.filter((r) => !text || JSON.stringify(r).toLowerCase().includes(text));
    body.replaceChildren(...matching.slice(0, limit).map(rowFn));
    const remaining = matching.length - Math.min(limit, matching.length);
    count.textContent = `Showing ${Math.min(limit, matching.length)} of ${matching.length} row(s)`
      + (text ? ` matching "${filter.value.trim()}"` : "") + ".";
    more.textContent = `Show ${Math.min(remaining, TABLE_PAGE)} more (${remaining} not shown)`;
    more.classList.toggle("hidden", remaining <= 0);
  };
  filter.addEventListener("input", () => { limit = TABLE_PAGE; draw(); });
  more.addEventListener("click", () => { limit += TABLE_PAGE; draw(); });
  draw();
  return [filter, el("div", { class: "table-wrap" }, el("table", { class: "vcf-filter-table" },
    el("thead", {}, el("tr", {}, header.map((h) => el("th", {}, h)))), body)), count, more];
}

/** The papers retrieved for the Literature agent, best first, with the abstract sentences it was given. */
function literatureList(items) {
  const papers = items.filter((i) => i.kind === "publication").sort((a, b) => (b.score ?? 0) - (a.score ?? 0));
  if (!papers.length) return el("p", { class: "muted" }, "None: no Tier 3 variant or level C–E pair, or nothing relevant found.");
  const ranker = papers[0].ranker === "MedCPT" ? "MedCPT Cross-Encoder" : "keyword score";
  return [el("p", { class: "muted small" }, `PubTator hits that name the gene (and the drug), re-ranked by ${ranker}; `
      + "the sentences are what the Literature agent read."),
    el("ol", { class: "papers" }, papers.map((p) => el("li", { class: "ev-row", "data-eid": p.id },
      el("a", { href: p.url, target: "_blank", rel: "noopener", class: "paper-title" }, p.title),
      el("div", { class: "muted small" }, [p.journal, p.year, `PMID ${p.pmid}`, `relevance ${p.score}`]
        .filter(Boolean).join(" · "), " ", refLink(p.id)),
      p.passages?.length
        ? el("ul", { class: "passages small" }, p.passages.map((s) => el("li", {}, el("span", { class: "muted" }, `S${s.n} `), s.text)))
        : el("p", { class: "muted small" }, "No abstract available: ranked on the title only."))))];
}

function renderAppendix(evidence) {
  const v = evidence.variants || {};
  const counts = v.counts || {};
  const lost = counts.dropped || {};
  const tracks = evidence.tracks || {};
  const audit = v.qc_audit || [];
  const low = v.low_priority || [];
  $("vcf-appendix").replaceChildren(
    el("div", { class: "qc-funnel" },
      el("span", { class: "step-box" }, `${counts.total ?? 0} variants`),
      ...Object.entries(DROP_STAGES).flatMap(([key, name]) => [el("span", { class: "arrow" }, "→"),
        el("span", { class: "step-box" }, `${name} −${lost[key] ?? 0}`)]),
      el("span", { class: "arrow" }, "→"),
      el("span", { class: "step-box" }, `Tier 1 ${counts.tier1 ?? 0} · Tier 2 ${counts.tier2 ?? 0} · Tier 3 ${counts.tier3 ?? 0} · low priority ${counts.low_priority ?? 0}`)),
    el("details", {}, el("summary", {}, `All tiered variants (${v.tiered?.length || 0})`),
      ...filterableTable(v.tiered || [], ["Tier", "Variant", "Position", "Reads", "Reason"], variantRow)),
    el("details", {}, el("summary", {}, `Low-priority variants: S below threshold, kept (${low.length})`),
      low.length ? scoreTable(low) : el("p", { class: "muted" }, "None.")),
    el("details", {}, el("summary", {}, `QC audit table: failed variants and reasons (${audit.length})`),
      ...filterableTable(audit, ["Step", "Variant", "Position", "Reads", "Reason"], (r) => el("tr", {},
        el("td", {}, DROP_STAGES[r.dropped_at] || r.dropped_at), el("td", {}, r.gene ? `${r.gene} ${r.protein_change || ""}` : "–"),
        el("td", {}, where(r)), el("td", {}, r.depth != null ? `${r.alt_reads}/${r.depth} (${pct(r.vaf)})` : "–"),
        el("td", { class: "small" }, r.reason)))),
    el("details", {}, el("summary", {}, `Retrieved literature (${(evidence.items || []).filter((i) => i.kind === "publication").length})`),
      literatureList(evidence.items || [])),
    el("details", {}, el("summary", {}, `Full established list (${tracks.established?.length || 0})`),
      tracks.established?.length ? candidateTable(tracks.established, evidence.australian_availability) : el("p", { class: "muted" }, "None.")),
    el("details", {}, el("summary", {}, `Full exploratory list (${tracks.exploratory?.length || 0})`),
      tracks.exploratory?.length ? candidateTable(tracks.exploratory, evidence.australian_availability) : el("p", { class: "muted" }, "None.")),
  );
}

function renderLimits(notes, parameters) {
  const qc = parameters?.qc;
  const t3 = parameters?.tier3;
  $("vcf-limits").replaceChildren(
    el("ul", {}, notes.map((n) => el("li", {}, n))),
    qc ? el("p", { class: "small muted" }, `Parameters used: DP ≥ ${qc.min_depth}, ALT reads ≥ ${qc.min_alt_reads}, VAF ≥ `
      + `${pct(qc.min_vaf)}, gnomAD AF ≤ ${pct(qc.max_population_af)}; Tier 3 S = ${t3.weights.F}·F + ${t3.weights.R}·R + `
      + `${t3.weights.B}·B, low priority below ${t3.min_score}.`) : "",
    el("p", { class: "disclaimer" }, "Research and teaching use only, with synthetic test data. Not clinical advice."));
}

// ---------- In-silico mutagenesis (AlphaGenome) ----------

const ismCache = new Map(); // "chrom:pos:alt" -> response, for this page session
let ismRequest = 0;

/** ISM targets: the variants that passed QC (tiered first, then low priority), capped by the backend setting. */
function renderIsm(evidence) {
  const box = $("vcf-ism");
  const settings = options?.ism;
  const v = evidence.variants || {};
  const targets = [...(v.tiered || []), ...(v.low_priority || [])];
  const assembly = evidence.input?.assembly || "GRCh38";
  box.replaceChildren();
  if (!settings?.enabled) {
    box.append(el("p", { class: "muted" }, "AlphaGenome is not configured on this server. ", settings?.reason || ""));
    return;
  }
  if (!targets.length) {
    box.append(el("p", { class: "muted" }, "No variant passed QC, so there is nothing to map."));
    return;
  }
  if (!/^(GRCh38|hg38)$/i.test(assembly)) {
    box.append(el("p", { class: "muted" }, `AlphaGenome supports GRCh38 only; this file is on ${assembly}.`));
    return;
  }
  const shown = targets.slice(0, settings.max_variants);
  const panel = el("div");
  const buttons = shown.map((t, i) => el("button", { type: "button", class: "secondary", onclick: () => select(i) },
    t.notation || `${t.gene || "?"} ${t.protein_change || ""}`));
  const select = (i) => {
    buttons.forEach((b, j) => b.classList.toggle("active", i === j));
    loadIsm(shown[i], assembly, panel);
  };
  box.append(
    el("p", { class: "muted small" }, `Each cell: AlphaGenome's predicted ${settings.output_label} when that base is `
      + `changed to the row's base, on the reference sequence (±${settings.flank} bp). Red raises it, blue lowers it; `
      + "the outlined cell is the patient's own change. AlphaGenome models regulation (expression, splicing, chromatin), "
      + "not protein function."),
    targets.length > shown.length ? el("p", { class: "muted small" },
      `Showing the first ${shown.length} of ${targets.length} QC-passed variants.`) : "",
    el("div", { class: "ism-picker" }, buttons),
    panel,
  );
  select(0);
}

async function loadIsm(t, assembly, panel) {
  const key = `${t.chrom}:${t.pos}:${t.alt}`;
  const ticket = ++ismRequest;
  if (!ismCache.has(key)) {
    panel.replaceChildren(el("p", { class: "muted" }, `Scoring ${(2 * options.ism.flank + 1) * 3} substitutions around `
      + `${where(t)} with AlphaGenome… this can take a minute.`));
    try {
      ismCache.set(key, await api.vcfIsm({ chrom: t.chrom, pos: t.pos, ref: t.ref, alt: t.alt, gene: t.gene, assembly }));
    } catch (err) {
      if (ticket === ismRequest) panel.replaceChildren(el("p", { class: "report-warning" }, `AlphaGenome ISM failed: ${err.message}`));
      return;
    }
  }
  if (ticket === ismRequest) panel.replaceChildren(...ismHeatmap(ismCache.get(key), t));
}

/** 3 decimals, or 2 significant digits in exponent form for small values (as AlphaGenome's own viewer shows them). */
function ismValue(x) {
  if (x === 0) return "0";
  return Math.abs(x) < 0.01 ? x.toExponential(1) : x.toFixed(3);
}

function ismHeatmap(data, t) {
  const n = data.positions.length;
  const max = data.max_abs || 1;
  const observed = data.variant?.snv ? data.variant : null;
  const grid = el("div", { class: "ism-grid", style: `grid-template-columns: 22px repeat(${n}, minmax(64px, 1fr))` });
  for (const base of "ACGT") {
    grid.append(el("div", { class: "ism-base" }, base));
    data.positions.forEach((pos, i) => {
      const x = data.scores[base][i];
      if (data.ref[i] === base || x == null) {
        grid.append(el("div", { class: "ism-cell ref", title: `${data.chrom}:${pos} reference ${data.ref[i] || "?"}` }, "ref"));
        return;
      }
      const share = Math.round(Math.min(Math.abs(x) / max, 1) * 100);
      const colour = x >= 0 ? "--ism-pos" : "--ism-neg";
      const isObserved = observed && pos === observed.pos && base === observed.alt.toUpperCase();
      grid.append(el("div", {
        class: `ism-cell${share > 60 ? " light" : ""}${isObserved ? " observed" : ""}`,
        style: `background: color-mix(in srgb, var(${colour}) ${share}%, var(--surface))`,
        title: `${data.chrom}:${pos} ${data.ref[i]}>${base}: ${x}${isObserved ? " (patient variant)" : ""}`,
      }, ismValue(x)));
    });
  }
  grid.append(el("div"));
  data.positions.forEach((pos) => grid.append(el("div", { class: `ism-pos${pos === t.pos ? " centre" : ""}` },
    pos.toLocaleString("en-US"))));
  const tissue = data.tissue && data.tissue_matched ? `tissue ${data.tissue} (${data.tracks} tracks)`
    : `all tissues (${data.tracks} tracks${data.tissue ? `; none for ${data.tissue}` : ""})`;
  return [
    el("p", { class: "ism-meta small" }, el("strong", {}, t.notation || where(t)), ` · ${data.chrom}:${data.positions[0]}–`
      + `${data.positions[n - 1]} · ${data.output_label}${data.gene ? ` (${data.gene})` : ""} · ${tissue} · `
      + `${data.sequence_length} context${data.cached ? " · cached" : ""}`),
    el("div", { class: "ism-scroll" }, grid),
    el("div", { class: "ism-legend small muted" }, ismValue(-max), el("span", { class: "ramp" }), ismValue(max),
      el("span", {}, `· ${data.model}. Prediction for research use, not a pathogenicity call.`)),
  ];
}

/** The element behind a citation in the VCF results, or null. */
export function vcfEvidenceTarget(eid) {
  return document.querySelector(`#vcf-results [data-eids~="${CSS.escape(eid)}"]`)
    || document.querySelector(`#vcf-results .ev-row[data-eid="${CSS.escape(eid)}"]`);
}
