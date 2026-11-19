/**
 * Rule-based summaries for the busy reader, computed from the evidence bundle (never from the LLM output):
 *   - "At a glance": one line per decision (what to consider, genotype advice, interactions) above the AI summary
 *   - "Patient genotype": the entered pharmacogene results -> CPIC phenotype -> CPIC recommendation per drug
 */
import { el, focusEvidence } from "./util.js";

const GRADE_ORDER = { strong: 0, moderate: 1, supportive: 2, limited: 3 };
const CLASS_ORDER = { Strong: 0, Moderate: 1, Optional: 2 };
const SEVERITY_ORDER = { major: 0, moderate: 1, minor: 2 };
const titleCase = (s) => (s || "").toLowerCase().replace(/(^|[\s\-+/(])([a-z])/g, (m, p, c) => p + c.toUpperCase());

function refLink(eid) {
  return el("a", { class: "ref", href: "#", onclick: (e) => { e.preventDefault(); focusEvidence(eid); } }, eid);
}

function refs(eids) {
  return eids.length ? [" ", ...eids.flatMap((eid, i) => (i ? [" ", refLink(eid)] : [refLink(eid)]))] : [];
}

/** "PBS: authority required" / "PBS listed" / "not PBS-listed"; null when PBS was not checked for the drug. */
export function pbsText(drug, availability) {
  const a = (availability || []).find((x) => (x.drug || "").toUpperCase() === (drug || "").toUpperCase());
  if (!a) return null;
  if (!a.pbs_listed) return "not PBS-listed";
  const types = [...new Set((a.items || []).map((i) => i.benefit_type).filter(Boolean))];
  return types.length === 1 && types[0] !== "Unrestricted" ? `PBS: ${types[0].toLowerCase()}` : "PBS listed";
}

function line(kind, label, ...content) {
  return el("li", { class: `glance-${kind}` }, el("span", { class: "glance-label" }, label), el("span", {}, ...content));
}

function box(lines, note) {
  return el("div", { class: "glance", role: "note", "aria-label": "At a glance" },
    el("p", { class: "glance-title" }, "At a glance"),
    el("ul", {}, lines),
    el("p", { class: "muted small" }, note));
}

/** The genotype recommendations to headline: one per drug, strongest classification, general population first. */
function headlineGenotype(items, ids) {
  const recs = ids.map((id) => items.get(id)).filter(Boolean)
    .filter((r) => r.classification && !/^no recommendation/i.test(r.classification))
    .sort((a, b) => (CLASS_ORDER[a.classification] ?? 9) - (CLASS_ORDER[b.classification] ?? 9));
  const seen = new Set();
  return recs.filter((r) => !seen.has(r.drug) && seen.add(r.drug));
}

function worstInteraction(alerts) {
  return [...(alerts || [])].sort((a, b) => (SEVERITY_ORDER[a.severity] ?? 9) - (SEVERITY_ORDER[b.severity] ?? 9))[0];
}

function interactionLine(alerts) {
  const worst = worstInteraction(alerts);
  if (!worst) return null;
  const more = alerts.length > 1 ? ` (+${alerts.length - 1} more)` : "";
  return line(worst.severity === "major" ? "danger" : "warn", "Interaction",
    el("strong", {}, `${worst.severity[0].toUpperCase()}${worst.severity.slice(1)}: `),
    `${titleCase(worst.drug)} ↔ ${titleCase(worst.other)}. ${worst.summary || ""}${more}`, ...refs([worst.evidence_id]));
}

// ---------- gene / drug search ----------

export function renderGlance(container, query) {
  container.replaceChildren();
  const evidence = query.evidence || {};
  if (evidence.outcome?.status === "none" || !evidence.table) return;
  const isDrug = query.entity_type === "drug";
  const items = new Map((evidence.items || []).map((i) => [i.id, i]));
  const rows = [...evidence.table].sort((a, b) => (GRADE_ORDER[a.grade] ?? 9) - (GRADE_ORDER[b.grade] ?? 9));
  const lines = [];

  // 1 The patient's genotype, when given: the CPIC recommendation per drug.
  const genotypes = evidence.genotypes;
  const recs = genotypes ? headlineGenotype(items, genotypes.recommendations || []) : [];
  for (const r of recs.slice(0, 3)) {
    const avoid = /\bavoid|not recommended|alternative|contraindicat/i.test(r.recommendation || "");
    lines.push(line(avoid ? "danger" : "info", "Genotype",
      el("strong", {}, `${titleCase(r.drug)}: `), `${r.short_recommendation} `,
      el("span", { class: "muted" }, `(${r.phenotype}; CPIC ${r.classification.toLowerCase()}${r.population !== "general" ? `, ${r.population}` : ""})`),
      ...refs([r.id])));
  }
  if (recs.length > 3) lines.push(line("info", "", `${recs.length - 3} more genotype recommendations under Pharmacogenomics.`));
  if (genotypes && !recs.length) {
    const ok = (genotypes.entered || []).filter((g) => g.status === "ok");
    lines.push(line("info", "Genotype", ok.length
      ? `${ok.map((g) => `${g.gene} ${g.phenotype}`).join("; ")}: no CPIC recommendation for the drugs in this result.`
      : "The genotypes entered could not be interpreted (see Pharmacogenomics)."));
  }

  // 2 The best-supported partners (drugs for a gene search, genes for a drug search), with PBS status.
  const best = [];
  const seen = new Set();
  for (const r of rows) {
    const name = isDrug ? r.gene : r.drug;
    if (!name || seen.has(name.toUpperCase()) || !["strong", "moderate"].includes(r.grade)) continue;
    seen.add(name.toUpperCase());
    best.push(r);
  }
  if (best.length) {
    lines.push(line("ok", "Strongest evidence", ...best.slice(0, 3).flatMap((r, i) => {
      const pbs = isDrug ? null : pbsText(r.drug, evidence.australian_availability);
      return [i ? "; " : "", el("strong", {}, isDrug ? r.gene : titleCase(r.drug)),
        ` (${r.grade_label}: ${r.level || r.grade_reason}${pbs ? ` · ${pbs}` : ""})`, ...refs([r.evidence_id].filter(Boolean))];
    }), best.length > 3 ? ` and ${best.length - 3} more` : ""));
  } else if (rows.length) {
    lines.push(line("info", "Strongest evidence", `No pair graded strong or moderate; ${rows.length} weaker pair(s) in the table.`));
  }

  // 3 A genotype-guided drug, but no genotype entered: say what to enter.
  if (!genotypes) {
    const guideline = [...items.values()].find((i) => i.kind === "guideline" && /^A/.test(i.cpic_level || ""));
    if (guideline) {
      lines.push(line("info", "Genotype", `CPIC level ${guideline.cpic_level} guideline for ${guideline.gene} – `
        + `${titleCase(guideline.drug)}: enter the patient's ${guideline.gene} result under the search box to get the `
        + "dosing recommendation.", ...refs([guideline.id])));
    }
  }

  // 4 Safety: the worst interaction with the current medications, and withdrawn / boxed-warning drugs.
  const interaction = interactionLine(evidence.safety?.interactions);
  if (interaction) lines.push(interaction);
  const warned = [...new Set(best.filter((r) => r.warnings?.length).map((r) => titleCase(r.drug)))];
  if (warned.length) lines.push(line("warn", "Warning", `${warned.join(", ")}: ChEMBL boxed warning or withdrawal (see Drug safety).`));
  for (const g of genotypes?.entered || []) {
    for (const note of g.phenoconversion || []) lines.push(line("warn", "Phenoconversion", `${g.gene}: ${note}`));
  }

  if (lines.length) {
    container.append(box(lines, "Rule-based summary of the sections below (not written by the AI). Research use "
      + "only; check against the full guideline and the product information before prescribing."));
  }
}

// ---------- patient VCF ----------

function levelText(c) {
  if (c.best) return `CIViC ${c.best.level}, ${c.best.disease_match}`;
  if (c.oncokb_level) return `OncoKB ${c.oncokb_level.replace("LEVEL_", "")}`;
  return c.track_reason;
}

export function renderVcfGlance(container, evidence) {
  container.replaceChildren();
  if (evidence.outcome?.status !== "found") return;
  const established = evidence.tracks?.established || [];
  const usable = established.filter((c) => !c.strong_resistance);
  const resistant = established.filter((c) => c.strong_resistance);
  const lines = [];
  const option = (c) => {
    const pbs = pbsText(c.drug, evidence.australian_availability);
    return [el("strong", {}, c.drug), ` (${levelText(c)}; ${c.variants.join(" + ")}${pbs ? ` · ${pbs}` : ""})`,
      ...refs(c.evidence_ids.slice(0, 1))];
  };
  if (usable.length) {
    lines.push(line("ok", "First consideration", ...option(usable[0])));
    if (usable.length > 1) {
      lines.push(line("info", "Alternatives", ...usable.slice(1, 3).flatMap((c, i) => [i ? "; " : "", ...option(c)]),
        usable.length > 3 ? ` and ${usable.length - 3} more` : ""));
    }
  } else {
    lines.push(line("warn", "First consideration", evidence.outcome.no_targeted_therapy
      || "No established targeted therapy without resistance for these variants."));
  }
  if (resistant.length) {
    lines.push(line("danger", "Not recommended", `${resistant.slice(0, 4).map((c) => c.drug).join(", ")}`
      + `${resistant.length > 4 ? ` and ${resistant.length - 4} more` : ""} (resistance from the patient's variants).`));
  }
  const top = usable[0];
  const topAlerts = (top?.safety?.interactions || []).map((a) => ({ ...a, drug: a.drug || top.drug }));
  const interaction = interactionLine(topAlerts.length ? topAlerts : evidence.safety?.interactions);
  if (interaction) lines.push(interaction);
  if (top?.safety?.warnings?.length) lines.push(line("warn", "Warning", `${top.drug}: ${top.safety.warnings.join("; ")}`));
  container.append(box(lines, "Rule-based summary of the ranked tracks below (not written by the AI). Synthetic test "
    + "data; fusions, copy-number changes and PD-L1 are not covered."));
}

// ---------- the patient's genotypes (Pharmacogenomics section) ----------

export function renderGenotypes(container, evidence) {
  container.replaceChildren();
  const block = evidence.genotypes;
  if (!block) return 0;
  const items = new Map((evidence.items || []).map((i) => [i.id, i]));
  const recs = (block.recommendations || []).map((id) => items.get(id)).filter(Boolean);
  container.append(
    el("h3", { class: "sub-head" }, "Patient genotype → CPIC recommendation"),
    el("ul", { class: "genotype-list small" }, (block.entered || []).map((g) => el("li", {},
      el("strong", {}, g.input), g.status === "ok"
        ? [" → ", el("span", { class: "level-badge tier-2" }, g.phenotype),
           g.diplotype && g.diplotype !== g.input.replace(`${g.gene} `, "") ? el("span", { class: "muted" }, ` (CPIC diplotype ${g.diplotype})`) : "",
           g.activity_score ? el("span", { class: "muted" }, ` · activity score ${g.activity_score}`) : ""]
        : [" ", el("span", { class: "sev-badge moderate" }, "not interpreted"), el("span", { class: "muted" }, ` ${g.note || ""}`)],
      ...(g.phenoconversion || []).map((n) => el("div", { class: "phenoconversion" }, `⚠ ${n}`))))));
  if (recs.length) {
    container.append(el("div", { class: "table-wrap" }, el("table", { class: "genotype-table" },
      el("thead", {}, el("tr", {}, ["Drug", "Phenotype", "Population", "Recommendation", "Strength", "Ref"].map((h) => el("th", {}, h)))),
      el("tbody", {}, recs.map((r) => el("tr", { class: "ev-row", "data-eid": r.id },
        el("td", {}, el("strong", {}, titleCase(r.drug))),
        el("td", {}, r.phenotype),
        el("td", { class: "small" }, r.population),
        el("td", { class: "small" }, r.recommendation,
          Object.keys(r.implications || {}).length
            ? el("div", { class: "muted" }, Object.entries(r.implications).map(([gene, t]) => `${gene}: ${t}`).join(" ")) : ""),
        el("td", {}, r.classification ? el("span", { class: "badge" }, r.classification) : ""),
        el("td", { class: "small" }, r.url ? el("a", { href: r.url, target: "_blank", rel: "noopener" }, "CPIC ↗") : "",
          " ", refLink(r.id))))))));
  } else if ((block.entered || []).some((g) => g.status === "ok")) {
    container.append(el("p", { class: "muted small" }, "No CPIC recommendation applies to the drugs in this result for these genotypes."));
  }
  for (const i of block.incomplete || []) {
    container.append(el("p", { class: "muted small" }, `${titleCase(i.drug)}: CPIC's recommendation also needs the `
      + `patient's ${i.needs.join(" and ")} result.`));
  }
  container.append(el("p", { class: "muted small" }, "Phenotypes come from CPIC's diplotype tables; recommendations "
    + "are CPIC's, matched exactly to the phenotype. Check the full guideline for dosing details."));
  return recs.length;
}
