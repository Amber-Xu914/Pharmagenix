/**
 * The workflow around the results (backend: services/workflow/):
 *  - input confirmation when the gene is unknown or ambiguous (stage 1)
 *  - the stage-by-stage trace of what ran (stages 1–6)
 *  - the verdict when no interaction was found (stage 4)
 *  - the gene profile card (stage 2)
 *
 * Everything here comes from the rule-based evidence bundle, never from the LLM output.
 */
import { GRADES, el, focusEvidence } from "./util.js";

export const SOURCE_NAMES = {
  dgidb: "DGIdb", chembl: "ChEMBL", cpic: "CPIC", pharmgkb: "PharmGKB",
  pubtator: "PubTator", drugbank: "DrugBank", pbs: "PBS", uniprot: "UniProt",
};

const STAGES = [
  ["resolution", "Gene resolution"],
  ["profiles", "Gene profile"],
  ["retrieval", "DGI retrieval"],
  ["evidence", "Evidence integration"],
  ["analysis", "Analysis (LLM)"],
  ["report", "Report"],
];
const STATUS_ICONS = { done: "✓", warning: "!", skipped: "–", failed: "✗" };

function refLink(eid, label = eid) {
  return el("a", { class: "ref", href: "#", onclick: (e) => { e.preventDefault(); focusEvidence(eid); } }, label);
}

export function gradeBadge(grade, title) {
  if (!grade) return "";
  return el("span", { class: `grade-badge grade-${grade}`, title: title || "" }, GRADES[grade] || grade);
}

// ---------- Stage 1: input confirmation ----------

/**
 * problems: [{input, reason: "not_found" | "ambiguous", suggestions: [{name, type, description}]}].
 * onPick(input, suggestion) replaces one entry; onSearchAsTyped() runs the query unchanged.
 */
export function renderResolution(container, problems, { onPick, onSearchAsTyped }) {
  const rows = problems.map((p) => {
    const row = el("div", { class: "resolution-row" },
      el("p", {}, el("strong", {}, `“${p.input}”`),
        p.reason === "ambiguous" ? " is an alias of more than one gene. Which did you mean?"
          : p.suggestions.length ? " was not found as a gene. Did you mean:" : " was not found in HGNC, and there is no close match."),
      p.suggestions.length ? el("div", { class: "chips" }, p.suggestions.map((s) => el("button", {
        type: "button", class: "suggestion",
        onclick: () => { row.remove(); onPick(p.input, s, !container.querySelector(".resolution-row")); },
      }, el("span", { class: "suggestion-type" }, "🧬"), ` ${s.name}`,
        s.description ? el("span", { class: "muted small" }, ` · ${s.description}`) : ""))) : "");
    return row;
  });
  container.replaceChildren(
    el("p", { class: "resolution-title" }, "Please confirm your input before searching"),
    ...rows,
    el("div", { class: "row small" },
      el("button", { type: "button", class: "secondary small", onclick: onSearchAsTyped }, "Search as typed anyway"),
      el("span", { class: "muted" }, "or correct the search box and press Explore.")),
  );
  container.classList.remove("hidden");
}

// ---------- Stages 1–6: the trace ----------

function analysisStage(query) {
  if (["none", "tier3_only"].includes(query.evidence?.outcome?.status)) {
    const what = query.entity_type === "vcf" ? "no tiered variant" : "no known interaction";
    return { status: "skipped", detail: `${what}, so no LLM-generated claims` };
  }
  const primary = [...query.reports].reverse().find((r) => r.role === "primary");
  if (!primary) return { status: "skipped", detail: "no report" };
  const label = { success: "done", flagged: "warning", failed: "failed" }[primary.status] || "done";
  const detail = primary.status === "failed" ? "generation failed"
    : `${primary.model}${primary.status === "flagged" ? " · flagged by the citation check" : ""}`;
  return { status: label, detail };
}

/** stages: [key, label] in order (the gene workflow's by default; vcf.js passes Workflow 4's). */
export function renderTrace(container, query, stages = STAGES) {
  const trace = query.evidence?.trace;
  container.classList.toggle("hidden", !trace);
  if (!trace) return; // queries saved before the workflow existed
  const byStage = Object.fromEntries(trace.map((s) => [s.stage, s]));
  byStage.analysis = analysisStage(query);
  const outcome = query.evidence.outcome?.status;
  byStage.report = {
    status: "done",
    detail: outcome === "none" ? "sources checked, nothing to report" : "summary · table · network · references",
  };
  container.replaceChildren(el("ol", { class: "stepper", style: `--steps:${stages.length}` }, stages.map(([key, label], i) => {
    const stage = byStage[key] || { status: "skipped", detail: "" };
    return el("li", { class: `step ${stage.status}`, title: stage.detail },
      el("span", { class: "step-num", "aria-hidden": "true" }, STATUS_ICONS[stage.status] || i + 1),
      el("span", { class: "step-text" },
        el("span", { class: "step-label" }, `${i + 1}. ${label}`),
        el("span", { class: "step-detail" }, stage.detail)));
  })));
}

// ---------- Stage 4: the verdict ----------

export function sourcesChecked(outcome, labels = SOURCE_NAMES) {
  const names = (keys) => [...new Set((keys || []).map((k) => labels[k] || k))].join(", ");
  return [
    el("p", { class: "small" }, "Sources checked: ", names(outcome.checked_sources) || "none"),
    outcome.unavailable_sources?.length
      ? el("p", { class: "report-warning" },
        `⚠ ${names(outcome.unavailable_sources)} could not be reached, so this result may be incomplete.`)
      : "",
  ];
}

/** Shown only when nothing was found; otherwise the AI summary and the table speak for themselves. */
export function renderVerdict(container, query) {
  const outcome = query.evidence?.outcome;
  container.replaceChildren();
  if (outcome?.status !== "none") return;
  container.append(el("div", { class: "verdict none" },
    el("p", { class: "verdict-title" }, "No known interaction"),
    el("p", {}, `No drug–gene interactions were found for ${query.entities.join(", ")} with the selected `
      + "relationship types. No AI summary was generated, so no claims are made beyond the sources checked. "
      + "No record is not proof that there is no interaction."),
    ...sourcesChecked(outcome)));
}

// ---------- Stage 2: the gene profile ----------

function geneCard(p) {
  const protein = p.protein || {};
  const fn = protein.function || "";
  return el("article", { class: "profile-card ev-row", "data-eid": p.id },
    el("header", {},
      el("span", { class: "type-badge gene" }, "Gene"),
      el("h3", {}, p.entity),
      el("span", { class: "muted" }, protein.protein_name || p.full_name || ""),
      el("span", { class: "spacer" }),
      refLink(p.id)),
    el("dl", {},
      p.full_name ? [el("dt", {}, "Official name"), el("dd", {}, p.full_name)] : "",
      p.aliases?.length ? [el("dt", {}, "Aliases"), el("dd", {}, p.aliases.join(", "))] : "",
      p.location ? [el("dt", {}, "Location"), el("dd", {}, `Chr ${p.location}${p.locus_type ? ` · ${p.locus_type}` : ""}`)] : "",
      fn ? [el("dt", {}, "Function"), el("dd", {}, fn.length > 420
        ? el("details", {}, el("summary", {}, `${fn.slice(0, 420)}…`), fn.slice(420)) : fn)] : "",
      protein.domains?.length ? [el("dt", {}, "Domains"), el("dd", {}, protein.domains.map((d) =>
        el("span", { class: "ev-badge" }, `${d.name} (${d.start}–${d.end})`)))] : "",
      protein.locations?.length ? [el("dt", {}, "Cellular location"), el("dd", {}, protein.locations.join(", "))] : ""),
    el("p", { class: "small" }, protein.url
      ? el("a", { href: protein.url, target: "_blank", rel: "noopener" }, `UniProt ${protein.accession} ↗`)
      : el("span", { class: "muted" }, "UniProt details unavailable; showing HGNC data.")));
}

export function renderProfiles(container, items) {
  const profiles = items.filter((i) => i.kind === "profile");
  container.replaceChildren(profiles.length
    ? el("div", { class: "profile-grid" }, profiles.filter((p) => p.entity_type === "gene").map(geneCard))
    : el("p", { class: "muted" }, "No profile available (this query was run before profiles were added)."));
}
