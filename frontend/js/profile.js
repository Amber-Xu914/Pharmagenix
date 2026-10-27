/**
 * The results page around the AI answer: gene header (identity, links, headline numbers), rule-based
 * pharmacogenomic alerts, and the Pharmacogenomics and Literature sections.
 *
 * Everything here is computed from the evidence bundle, never from the LLM output.
 */
import { el, focusEvidence, formatDate } from "./util.js";
import { SOURCE_NAMES } from "./workflow.js";

const PGKB_LEVEL_ORDER = { "1A": 0, "1B": 1, "2A": 2, "2B": 3, "3": 4, "4": 5 };
const CPIC_LEVEL_ORDER = { A: 0, "A/B": 1, B: 2, "B/C": 3, C: 4, "C/D": 5, D: 6 };
// The profile source only appears when it ran.
const OPTIONAL_SOURCES = new Set(["uniprot"]);
const SOURCE_STATES = { ok: "✓", error: "✗ failed", skipped: "not requested", disabled: "not enabled" };

const upper = (value) => (value || "").toString().toUpperCase();
const plural = (n, word) => `${n} ${word}${n === 1 ? "" : "s"}`;

// ---------- The searched gene ----------

/** The searched gene: how the input was resolved, plus header details looked up by the backend. */
export function queryGene(query) {
  const entry = query.evidence?.normalisation?.[0];
  if (!entry) return { input: query.entities[0], name: upper(query.entities[0]), type: null, details: null };
  return { ...entry, details: query.entity_details?.[0] || null };
}

// ---------- Headline numbers ----------

/** Graded pair rows include pharmacogenomic-only pairs; rows saved before grading listed those under PGx only. */
export function interactionRows(table) {
  return table.filter((r) => r.evidence_ids || r.category !== "snp");
}

function bestBy(values, order) {
  return values.filter((v) => v in order).sort((a, b) => order[a] - order[b])[0] || null;
}

/** Numbers shown in the header and the section nav. Each links to the section that lists them. */
export function headlineNumbers(evidence, entityType = "gene") {
  const rows = interactionRows(evidence.table || []);
  const items = evidence.items || [];
  const availability = evidence.australian_availability;
  // A gene search counts the drugs it found; a drug search counts the genes.
  const partners = new Set(rows.map((r) => upper(entityType === "drug" ? r.gene : r.drug)).filter(Boolean));
  const graded = rows.every((r) => r.evidence_ids); // rows saved before stage 5 grading have no evidence_ids
  const supported = graded ? rows.filter((r) => ["strong", "moderate"].includes(r.grade)).length
    : rows.filter((r) => r.category !== "other").length;
  const guidelines = items.filter((i) => i.kind === "guideline");
  const annotations = items.filter((i) => i.kind === "variant_annotation");
  const papers = items.filter((i) => i.kind === "publication");
  const listed = (availability || []).filter((a) => a.pbs_listed).length;
  const bestCpic = bestBy(guidelines.map((g) => g.cpic_level), CPIC_LEVEL_ORDER);
  const bestPgkb = bestBy(annotations.map((a) => a.level), PGKB_LEVEL_ORDER);

  return {
    counts: {
      interactions: rows.length,
      pgx: guidelines.length + annotations.length,
      literature: papers.length,
      pbs: availability ? listed : null,
    },
    tiles: [
      { value: partners.size, label: entityType === "drug" ? "Genes" : "Drugs", sub: `${rows.length} interactions`,
        href: "#sec-interactions" },
      graded
        ? { value: supported, label: "Well supported", sub: "strong or moderate evidence", href: "#sec-interactions" }
        : { value: supported, label: "Known action", sub: "inhibit, induce, bind…", href: "#sec-interactions" },
      { value: guidelines.length, label: "CPIC guidelines", sub: bestCpic ? `best level ${bestCpic}` : "none", href: "#sec-pgx" },
      { value: annotations.length, label: "PGx annotations", sub: bestPgkb ? `top level ${bestPgkb}` : "PharmGKB", href: "#sec-pgx" },
      { value: papers.length, label: "Papers", sub: "PubTator", href: "#sec-literature" },
      availability
        ? { value: listed, label: "PBS-listed drugs", sub: `of ${availability.length} checked`, href: "#sec-pbs" }
        : { value: "–", label: "PBS-listed drugs", sub: "not imported", href: "#sec-pbs" },
    ],
  };
}

// ---------- Rule-based alerts (shown in the AI card, but not written by the AI) ----------

/** CPIC level A/B pairs and PharmGKB level 1A/1B annotations: the evidence strong enough to act on. */
export function pgxAlerts(items) {
  const alerts = [];
  const guidelines = items.filter((i) => i.kind === "guideline");
  for (const [prefix, text] of [
    ["A", "CPIC level A: genotype should be used to guide prescribing of"],
    ["B", "CPIC level B: genotype could be used to guide prescribing of"],
  ]) {
    const hits = guidelines.filter((g) => (g.cpic_level || "").startsWith(prefix)); // "A/B" counts as A
    if (hits.length) alerts.push({ text, refs: hits.map((g) => ({ label: `${g.drug} (${g.gene})`, eid: g.id })) });
  }
  const strong = items.filter((i) => i.kind === "variant_annotation" && ["1A", "1B"].includes(i.level));
  if (strong.length) {
    alerts.push({
      text: "PharmGKB level 1A/1B clinical annotations (high-level evidence):",
      refs: strong.map((a) => ({ label: `${a.variant} – ${a.drugs.join(", ")}`, eid: a.id })),
    });
  }
  return alerts;
}

export function renderAlerts(alerts) {
  if (!alerts.length) return null;
  return el("div", { class: "pgx-alert", role: "note" },
    el("p", { class: "pgx-alert-title" }, "⚠ Clinically relevant pharmacogenomic evidence"),
    el("ul", {}, alerts.map((a) => el("li", {}, `${a.text} `,
      a.refs.slice(0, 6).flatMap((ref, i) => [i ? ", " : "", evidenceLink(ref.label, ref.eid)]),
      a.refs.length > 6 ? ` and ${a.refs.length - 6} more` : ""))),
    el("p", { class: "muted small" }, "From CPIC and PharmGKB data, not generated by AI."));
}

function evidenceLink(label, eid) {
  return el("a", { href: "#", onclick: (e) => { e.preventDefault(); focusEvidence(eid); } }, label);
}

// ---------- Gene header ----------

function geneLinks(name, d) {
  return [
    d?.hgnc_id && ["HGNC", `https://www.genenames.org/data/gene-symbol-report/#!/hgnc_id/${d.hgnc_id}`],
    d?.entrez_id && ["NCBI Gene", `https://www.ncbi.nlm.nih.gov/gene/${d.entrez_id}`],
    d?.uniprot_ids?.[0] && ["UniProt", `https://www.uniprot.org/uniprotkb/${d.uniprot_ids[0]}`],
    d?.ensembl_gene_id && ["Open Targets", `https://platform.opentargets.org/target/${d.ensembl_gene_id}`],
    ["DGIdb", `https://dgidb.org/genes/${encodeURIComponent(name)}`],
  ].filter(Boolean);
}

function drugLinks(name, d) {
  return [
    d?.chembl_id && ["ChEMBL", `https://www.ebi.ac.uk/chembl/compound_report_card/${d.chembl_id}/`],
    ["DGIdb", `https://dgidb.org/drugs/${encodeURIComponent(name)}`],
    ["ClinPGx", `https://www.clinpgx.org/search?query=${encodeURIComponent(name)}`],
  ].filter(Boolean);
}

function describeGene(gene) {
  const d = gene.details;
  if (gene.type === "drug") {
    const phase = d?.max_phase == null ? null : d.max_phase >= 4 ? "Approved (ChEMBL phase 4)" : `Max clinical phase ${d.max_phase}`;
    return {
      fullName: d?.full_name && d.full_name.toUpperCase() !== gene.name ? d.full_name : null,
      meta: [d?.molecule_type, phase, d?.first_approval && `First approved ${d.first_approval}`, gene.id],
      links: drugLinks(gene.name, d),
    };
  }
  if (gene.type !== "gene") return { fullName: null, meta: [], links: [] }; // the note explains why
  const locus = d?.locus_type === "gene with protein product" ? "Protein-coding gene" : d?.locus_type;
  return {
    fullName: d?.full_name,
    meta: [d?.location && `Chr ${d.location}`, locus, gene.id, d?.ensembl_gene_id,
           d?.uniprot_ids?.length && `UniProt ${d.uniprot_ids.join(", ")}`],
    links: geneLinks(gene.name, d),
  };
}

/** How the input was interpreted when that isn't obvious (P53 → TP53), and lookup warnings. */
function interpretationNote(gene) {
  if (gene.type && gene.name === upper(gene.input).trim() && !gene.note) return null;
  return el("p", { class: "eh-note small" }, "Input interpreted as: ", gene.type
    ? [`${gene.input} → ${gene.name}`, gene.matched_by ? ` (${gene.matched_by})` : "",
       gene.note ? el("span", { class: "report-warning" }, `; ${gene.note}`) : ""]
    : el("span", { class: "report-warning" }, `${gene.input}: ${gene.note || "not found in local reference data"}`));
}

function sourcePills(status) {
  return el("div", { class: "source-status" },
    el("span", { class: "muted small" }, "Sources:"),
    Object.entries(SOURCE_NAMES)
      .filter(([key]) => !OPTIONAL_SOURCES.has(key) || ["ok", "error"].includes(status[key]?.state))
      .map(([key, name]) => {
        const s = status[key] || { state: "skipped" };
        const label = s.state === "ok" ? `${name} ✓ ${s.count}` : `${name} ${SOURCE_STATES[s.state] || s.state}`;
        return el("span", { class: `source-pill ${s.state}`, title: s.error || s.version || "" }, label);
      }));
}

export function renderHeader(container, { query, gene, numbers }) {
  const { fullName, meta, links } = describeGene(gene);
  // replaceChildren() would print a null as the text "null", so optional parts are filtered out.
  container.replaceChildren(...[
    el("div", { class: "eh-top" },
      el("span"),
      el("span", { class: "muted small" },
        `Query #${query.id}${query.parent_id ? ` (follow-up to #${query.parent_id})` : ""} · ${formatDate(query.created_at)}`)),
    el("div", { class: "eh-title" },
      el("h2", {}, gene.name),
      el("span", { class: `type-badge ${gene.type || ""}` }, gene.type === "drug" ? "Drug" : gene.type ? "Gene" : "Unresolved"),
      links.length ? el("div", { class: "eh-links" }, links.map(([label, href]) =>
        el("a", { href, target: "_blank", rel: "noopener" }, `${label} ↗`))) : null),
    fullName ? el("p", { class: "eh-name" }, fullName) : null,
    meta.filter(Boolean).length ? el("p", { class: "eh-meta small" }, meta.filter(Boolean).join(" · ")) : null,
    interpretationNote(gene),
    el("div", { class: "kpis" }, numbers.tiles.map((t) => el("a", { class: "kpi", href: t.href },
      el("span", { class: "kpi-value" }, t.value),
      el("span", { class: "kpi-label" }, t.label),
      el("span", { class: "kpi-sub" }, t.sub)))),
    sourcePills(query.evidence?.source_status || {}),
  ].filter(Boolean));
}

// ---------- Pharmacogenomics ----------

function levelBadge(text, tier) {
  return el("span", { class: `level-badge tier-${tier}` }, text);
}

function cpicTier(level) {
  return /^A/.test(level) ? 1 : /^B/.test(level) ? 2 : 3;
}

function pgkbTier(level) {
  return /^1/.test(level) ? 1 : /^2/.test(level) ? 2 : 3;
}

function refLink(eid) {
  return el("a", { class: "ref", href: "#", onclick: (e) => { e.preventDefault(); focusEvidence(eid); } }, eid);
}

export function renderPgx(container, items, { requested }) {
  if (!requested) {
    container.replaceChildren(el("p", { class: "muted" },
      "Not requested. Tick “SNP-specific response” in the search to include CPIC guidelines and PharmGKB annotations."));
    return;
  }
  const guidelines = items.filter((i) => i.kind === "guideline")
    .sort((a, b) => (CPIC_LEVEL_ORDER[a.cpic_level] ?? 9) - (CPIC_LEVEL_ORDER[b.cpic_level] ?? 9));
  const annotations = items.filter((i) => i.kind === "variant_annotation")
    .sort((a, b) => (PGKB_LEVEL_ORDER[a.level] ?? 9) - (PGKB_LEVEL_ORDER[b.level] ?? 9));
  if (!guidelines.length && !annotations.length) {
    container.replaceChildren(el("p", { class: "muted" }, "No CPIC guidelines or PharmGKB clinical annotations found."));
    return;
  }

  const guidelineList = guidelines.map((g) => el("div", { class: "guideline ev-row", "data-eid": g.id },
    el("div", { class: "guideline-head" },
      el("strong", {}, `${g.gene} – ${g.drug}`),
      g.cpic_level && levelBadge(`CPIC ${g.cpic_level}`, cpicTier(g.cpic_level)),
      g.pgx_testing && el("span", { class: "muted small" }, `PGx testing: ${g.pgx_testing}`),
      el("span", { class: "spacer" }),
      g.url && el("a", { href: g.url, target: "_blank", rel: "noopener", class: "small" },
        g.guideline_name ? `${g.guideline_name} ↗` : "Guideline ↗"),
      refLink(g.id)),
    g.recommendations?.length ? el("details", { class: "recs" },
      el("summary", { class: "small" }, `${plural(g.recommendations.length, "recommendation")}`),
      el("ul", {}, g.recommendations.map((r) => el("li", { class: "small" },
        el("strong", {}, Object.entries(r.lookup || {}).map(([k, v]) => `${k}: ${v}`).join(", ") || "All patients"),
        r.population && r.population !== "general" ? el("span", { class: "muted" }, ` (${r.population})`) : "",
        ` — ${r.recommendation}`,
        r.classification ? el("span", { class: "badge" }, r.classification) : "")))) : null));

  const annotationTable = annotations.length && el("div", { class: "table-wrap" }, el("table", {},
    el("thead", {}, el("tr", {}, ["Level", "Variant", "Gene", "Drug", "Phenotype", "Summary", "Ref"].map((h) => el("th", {}, h)))),
    el("tbody", {}, annotations.map((a) => el("tr", { class: "ev-row", "data-eid": a.id },
      el("td", {}, levelBadge(a.level, pgkbTier(a.level))),
      el("td", {}, a.url ? el("a", { href: a.url, target: "_blank", rel: "noopener" }, a.variant) : a.variant),
      el("td", {}, a.genes.join(", ")),
      el("td", {}, a.drugs.join(", ")),
      el("td", {}, a.phenotype_categories.join(", ")),
      el("td", { class: "summary-cell" }, a.summary || ""),
      el("td", {}, refLink(a.id)))))));

  container.replaceChildren(
    guidelines.length ? el("h3", { class: "sub-head" }, `CPIC prescribing guidelines (${guidelines.length})`) : "",
    ...guidelineList,
    annotations.length ? el("h3", { class: "sub-head" }, `PharmGKB clinical annotations (${annotations.length})`) : "",
    annotationTable || "",
    el("p", { class: "muted small" },
      "CPIC level A/B: genotype should/could change prescribing. PharmGKB levels run from 1A (highest evidence) to 4."));
}

// ---------- Literature ----------

export function renderLiterature(container, items) {
  const papers = items.filter((i) => i.kind === "publication");
  if (!papers.length) {
    container.replaceChildren(el("p", { class: "muted" }, "No related papers found."));
    return;
  }
  container.replaceChildren(el("ol", { class: "papers" }, papers.map((p) => el("li", { class: "ev-row", "data-eid": p.id },
    el("a", { href: p.url, target: "_blank", rel: "noopener", class: "paper-title" }, p.title),
    el("div", { class: "muted small" },
      [p.authors?.length && `${p.authors.join(", ")}${p.authors.length >= 3 ? " et al." : ""}`,
       p.journal, p.year, `PMID ${p.pmid}`].filter(Boolean).join(" · "),
      p.query ? el("span", { class: "badge" }, p.query.replace(/ AND /g, " – ")) : "",
      " ", refLink(p.id))))));
}
