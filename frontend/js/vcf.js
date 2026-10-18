/**
 * Workflow 4: patient VCF + cancer type -> ranked candidate drugs (lung cancer prototype, synthetic test data only).
 * Backend: routes/vcf.py and services/variants/. QC, tiers, tracks and scores come from deterministic tools; the
 * Analysis agent explains them and the exploration agents work on the exploratory track only.
 */
import { api } from "./api.js";
import { NetworkView } from "./network.js";
import { renderReports } from "./summary.js";
import { el, focusEvidence } from "./util.js";
import { SOURCE_NAMES, renderTrace, sourcesChecked } from "./workflow.js";

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
const VCF_SOURCES = { civic: "CIViC", oncokb: "OncoKB", vep: "Ensembl VEP", ...SOURCE_NAMES };
const SOURCE_STATES = { ok: "✓", error: "✗ failed", skipped: "not run", disabled: "not enabled" };
const DROP_STAGES = { qc: "QC", germline: "Germline", consequence: "Consequence" };

const network = new NetworkView({
  container: $("vcf-network"), info: $("vcf-network-info"), legend: $("vcf-network-legend"),
  categories: ["variant_of", "sensitive", "gene_link", "resistant"],
});
let options = null;

function refLink(eid) {
  return el("a", { class: "ref", href: "#", onclick: (e) => { e.preventDefault(); focusEvidence(eid); } }, eid);
}

function refs(eids, max = 6) {
  return [eids.slice(0, max).map((eid, i) => [i ? ", " : "", refLink(eid)]), eids.length > max ? ` +${eids.length - max}` : ""];
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
  const typeLabel = (code) => {
    const t = options.cancer_types.find((x) => x.code === code);
    return t ? `${t.name} (${t.code})` : code;
  };
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
  $("vcf-examples").replaceChildren(...options.examples.map((x, i) => el("label", { class: "vcf-example" },
    el("input", { type: "radio", name: "vcf-example", value: x.id, checked: i === 0,
                  onchange: () => { $("vcf-cancer").value = typeLabel(x.cancer_type); } }),
    el("span", {}, el("span", { class: "code" }, x.cancer_type), ` ${x.label} `,
      el("a", { href: `/api/vcf/examples/${encodeURIComponent(x.id)}`, class: "small", title: "Download the mock VCF",
                onclick: (e) => e.stopPropagation() }, "VCF ↓")))));
  if (options.examples[0]) $("vcf-cancer").value = typeLabel(options.examples[0].cancer_type);
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
      if (compare) form.append("compare", "1");
      return run(() => api.vcfExplore(form), message);
    }
    const example = document.querySelector('input[name="vcf-example"]:checked')?.value;
    return run(() => api.vcfExploreExample({ example, cancer_type: cancerType, compare }), message);
  });

  $("vcf-ask-form").addEventListener("submit", (event) => {
    event.preventDefault();
    const id = $("vcf-results").dataset.queryId;
    if (!id) return;
    const body = { followup_question: $("vcf-followup").value, compare: $("vcf-ask-compare").checked };
    run(() => api.ask(id, body), "Regenerating the analysis and agent reports from the existing evidence…");
  });
  $("vcf-network-fit").addEventListener("click", () => network.fit());
}

function setMode(mode) {
  document.querySelectorAll(".mode-switch [data-mode]").forEach((b) => {
    b.classList.toggle("active", b.dataset.mode === mode);
    b.setAttribute("aria-selected", String(b.dataset.mode === mode));
  });
  document.querySelectorAll("[data-mode-pane]").forEach((p) => p.classList.toggle("hidden", p.dataset.modePane !== mode));
}

function source() {
  return document.querySelector(".vcf-source button.active")?.dataset.source || "example";
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
  $("vcf-results").dataset.queryId = query.id;
  setMode("vcf");
  renderHeader(query, evidence);
  renderTrace($("vcf-trace"), withAgentTrace(query), VCF_STAGES);
  renderVerdict(evidence);
  renderReports($("vcf-reports"), query, { title: "Analysis agent" });
  document.querySelector("#vcf-sec-overview .ask").classList.toggle("hidden", evidence.outcome?.status !== "found");
  renderEstablished(evidence);
  renderExploratory(query, evidence);
  renderCoMutations(evidence);
  renderAppendix(evidence);
  renderLimits(evidence.limitations || [], evidence.parameters);
  const tracks = evidence.tracks || {};
  $("vcf-count-established").textContent = tracks.established?.length || "";
  $("vcf-count-exploratory").textContent = tracks.exploratory?.length || "";
  // Cytoscape needs a visible container, so the network is drawn after the section is shown.
  requestAnimationFrame(() => network.render(evidence.network, 0));
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

function candidateTable(rows, availability) {
  const pbs = new Map((availability || []).map((a) => [a.drug.toUpperCase(), a]));
  return el("div", { class: "table-wrap" }, el("table", { class: "cand-table" },
    el("thead", {}, el("tr", {}, ["#", "Drug", "Target variant(s)", "Evidence", "Score", "Rationale", "Approved / PBS", "Refs"]
      .map((h) => el("th", {}, h)))),
    el("tbody", {}, rows.map((c) => {
      const listing = pbs.get(c.drug.split(" + ")[0].toUpperCase());
      return el("tr", { class: "ev-row", "data-eids": c.evidence_ids.join(" ") },
        el("td", { class: "rank" }, c.rank),
        el("td", { class: "drug" }, c.drug, c.mechanism ? el("div", { class: "muted small" }, c.mechanism) : ""),
        el("td", {}, c.variants.join(", ") || "–"),
        el("td", {}, levelBadge(c), c.best ? el("div", { class: "muted small" },
          `${c.best.disease_match} · ${c.best.specificity} match · ${c.n_evidence} item${c.n_evidence === 1 ? "" : "s"}`) : ""),
        el("td", {}, el("span", { class: "score" }, c.score),
          el("ul", { class: "score-parts" }, (c.score_parts || []).map((p) => el("li", {}, `${p.points > 0 ? "+" : ""}${p.points} ${p.label}`)))),
        el("td", { class: "small" }, c.track_reason,
          (c.co_occurrence || []).map((n) => el("div", { class: "note-co" }, `↔ ${n}`)),
          (c.resistance_notes || []).slice(0, 3).map((n) => el("div", { class: "note-resist" }, `⚠ ${n}`)),
          (c.conflicts || []).map((n) => el("div", { class: "report-warning" }, `⚠ ${n}`)),
          (c.literature || []).length ? el("div", { class: "muted" }, "Literature: ", refs(c.literature.map((p) => p.item_id), 3)) : ""),
        el("td", { class: "small" }, c.approved ? "Approved" : el("span", { class: "muted" }, "–"),
          listing ? el("div", {}, listing.pbs_listed ? "PBS listed" : el("span", { class: "muted" }, "Not on PBS")) : ""),
        el("td", { class: "small" }, refs(c.evidence_ids, 4),
          c.pmids?.length ? el("div", { class: "muted" }, `${c.pmids.length} PMID${c.pmids.length > 1 ? "s" : ""}`) : ""));
    }))));
}

function renderEstablished(evidence) {
  const rows = (evidence.tracks?.established || []).filter((c) => c.top);
  const avoid = evidence.resistance_only || [];
  const box = $("vcf-established");
  box.replaceChildren(
    evidence.outcome?.no_targeted_therapy ? el("p", { class: "no-tt" }, evidence.outcome.no_targeted_therapy) : "",
    rows.length ? candidateTable(rows, evidence.australian_availability)
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
        el("td", { class: "small" }, (v.literature || []).length ? refs(v.literature.map((p) => p.item_id), 3) : el("span", { class: "muted" }, "–")));
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

function filterableTable(rows, header, rowFn) {
  const filter = el("input", { type: "search", placeholder: "Filter…" });
  const body = el("tbody");
  const draw = () => {
    const text = filter.value.trim().toLowerCase();
    body.replaceChildren(...rows.filter((r) => !text || JSON.stringify(r).toLowerCase().includes(text)).slice(0, 400).map(rowFn));
  };
  filter.addEventListener("input", draw);
  draw();
  return [filter, el("div", { class: "table-wrap" }, el("table", { class: "vcf-filter-table" },
    el("thead", {}, el("tr", {}, header.map((h) => el("th", {}, h)))), body))];
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

/** The element behind a citation in the VCF results, or null. */
export function vcfEvidenceTarget(eid) {
  return document.querySelector(`#vcf-results [data-eids~="${CSS.escape(eid)}"]`)
    || document.querySelector(`#vcf-results .ev-row[data-eid="${CSS.escape(eid)}"]`);
}
