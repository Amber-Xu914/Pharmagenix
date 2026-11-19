/**
 * "Drug safety": drug–drug interactions with the current medications (and inside combination regimens) and ChEMBL
 * drug warnings, as found by the backend's fixed rules (services/safety.py). Shared by the gene / drug results
 * (main.js) and the VCF results (vcf.js).
 */
import { el } from "./util.js";

const SEVERITY_TITLE = {
  major: "Avoid or change the combination: the label says to avoid it, a strong inhibitor / inducer meets a sensitive substrate, or a strong inhibitor changes a pharmacogene's phenotype",
  moderate: "Monitor or adjust the dose",
  minor: "A weak inhibitor or inducer",
};
const RULE_LABEL = {
  "label class": "FDA label + FDA drug-interaction table",
  "label names drug": "FDA label",
  "FDA table": "FDA drug-interaction table",
  phenoconversion: "Drug–drug–gene (FDA table + CPIC)",
};

export function severityBadge(severity) {
  return el("span", { class: `sev-badge ${severity}`, title: SEVERITY_TITLE[severity] || "" }, severity);
}

/** The text input "Current medications" -> a list of names. */
export function medicationList(input) {
  return input.value.split(/[,;\n]+/).map((s) => s.trim()).filter(Boolean);
}

function source(a) {
  const label = a.label_title ? `${a.label_title} label${a.label_date ? ` (${a.label_date.slice(0, 4)})` : ""}` : "FDA table";
  return el("a", { href: a.url, target: "_blank", rel: "noopener" }, label);
}

function alertRow(a, items) {
  const item = items.get(a.evidence_id) || a;
  return el("tr", { class: "ev-row", "data-eid": a.evidence_id },
    el("td", {}, severityBadge(a.severity)),
    el("td", {}, el("strong", {}, a.drug), " ↔ ", a.other,
      el("div", { class: "muted small" }, a.context)),
    el("td", { class: "small" }, a.summary,
      item.label_text ? el("blockquote", { class: "label-quote" }, `“${item.label_text}”`) : "",
      item.fda_role && a.rule !== "label names drug" ? el("div", { class: "muted" }, `FDA table: ${item.fda_role}`) : ""),
    el("td", { class: "small" }, RULE_LABEL[a.rule] || a.rule, el("div", {}, source({ ...item, url: item.url || a.url }))),
    el("td", { class: "small" }, a.evidence_id));
}

/**
 * box: the section body. safety: evidence.safety. warned: [{drug, warnings}] drugs with ChEMBL warnings.
 * regimens: true for the VCF view (combination regimens are always checked).
 */
export function renderSafety(box, evidence, { warned = [], regimens = false, formHint = "", focus = null } = {}) {
  const safety = evidence.safety;
  box.replaceChildren();
  if (!safety) {
    box.append(el("p", { class: "muted" }, "This search was run before the safety checks were added."));
    return 0;
  }
  const items = new Map((evidence.items || []).map((i) => [i.id, i]));
  const meds = safety.medications || [];
  const alerts = safety.interactions || [];
  box.append(el("p", { class: "small" }, meds.length
    ? [`Checked ${safety.checked_drugs} drug${safety.checked_drugs === 1 ? "" : "s"} against the current medications: `,
       el("strong", {}, meds.map((m) => m.name).join(", ")), regimens ? "; and each combination regimen internally." : "."]
    : [regimens ? "Combination regimens were checked internally. " : "",
       el("span", { class: "muted" }, `No current medications were entered${formHint ? `: ${formHint}` : ""}.`)]));
  let shown = alerts.length;
  const table = (rows) => el("div", { class: "table-wrap" }, el("table", { class: "safety-table" },
    el("thead", {}, el("tr", {}, ["Severity", "Drugs", "Finding", "Source", "Ref"].map((h) => el("th", {}, h)))),
    el("tbody", {}, rows.map((a) => alertRow(a, items)))));
  if (alerts.length && focus) {
    // Clinical view: the interactions of the drugs being considered first; the rest folded away.
    const names = new Set(focus.flatMap((d) => d.split(" + ")).map((d) => d.trim().toLowerCase()));
    const involves = (a) => names.has((a.drug || "").toLowerCase()) || names.has((a.other || "").toLowerCase());
    const mine = alerts.filter(involves);
    const rest = alerts.filter((a) => !involves(a));
    shown = mine.length;
    box.append(mine.length ? table(mine) : el("p", { class: "muted" },
      "No interaction was found for the treatment options above."));
    if (rest.length) {
      box.append(el("details", { class: "more-alerts" },
        el("summary", { class: "small" }, `${rest.length} more interaction${rest.length === 1 ? "" : "s"} with other candidate drugs`),
        table(rest)));
    }
  } else if (alerts.length) {
    box.append(table(alerts));
  } else if (meds.length || regimens) {
    box.append(el("p", { class: "muted" }, "No pharmacokinetic interaction was found by the checks."));
  }
  if (warned.length) {
    box.append(el("h3", { class: "sub-head" }, `Drug warnings (ChEMBL): ${warned.length}`),
      el("ul", { class: "small" }, warned.map((w) => el("li", {}, el("strong", {}, w.drug), `: ${w.warnings.join("; ")}`))));
  }
  box.append(el("p", { class: "muted small" }, "Rules: the drug's FDA label (openFDA) and the FDA table of CYP / "
    + "transporter inhibitors, inducers and substrates; phenoconversion when a co-medication strongly inhibits a gene "
    + "the drug has a CPIC guideline for. Pharmacokinetic interactions only; not a complete interaction check."));
  return shown;
}

/** Short markers for a table row or candidate: warnings and the worst interaction. */
export function safetyMarkers(warnings = [], interactions = []) {
  const out = [];
  for (const w of warnings) {
    out.push(el("span", { class: `ev-badge ${w.startsWith("Withdrawn") ? "withdrawn" : "boxed"}`, title: w },
      w.startsWith("Withdrawn") ? "Withdrawn" : "Boxed warning"));
  }
  const order = ["major", "moderate", "minor"];
  const worst = interactions.map((a) => a.severity).sort((a, b) => order.indexOf(a) - order.indexOf(b))[0];
  if (worst) {
    const others = [...new Set(interactions.map((a) => a.other))].join(", ");
    out.push(el("span", { class: `sev-badge ${worst}`, title: interactions.map((a) => a.summary).join(" ") },
      `Interacts: ${others}`));
  }
  return out;
}
