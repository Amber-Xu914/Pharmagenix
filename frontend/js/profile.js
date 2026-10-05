/**
 * The results page around the AI answer: entity header (identity, links, headline numbers), rule-based
 * pharmacogenomic alerts, and the Pharmacogenomics and Literature sections.
 *
 * Everything here is computed from the evidence bundle, never from the LLM output.
 */
import { el, focusEvidence, formatDate } from "./util.js";

const TYPE_ICONS = { gene: "🧬", drug: "💊" };
const TYPE_LABELS = { gene: "Gene", drug: "Drug" };
const PGKB_LEVEL_ORDER = { "1A": 0, "1B": 1, "2A": 2, "2B": 3, "3": 4, "4": 5 };
const CPIC_LEVEL_ORDER = { A: 0, "A/B": 1, B: 2, "B/C": 3, C: 4, "C/D": 5, D: 6 };
const SOURCE_NAMES = {
  dgidb: "DGIdb", chembl: "ChEMBL", cpic: "CPIC", pharmgkb: "PharmGKB",
  pubtator: "PubTator", drugbank: "DrugBank", pbs: "PBS",
};
const SOURCE_STATES = { ok: "✓", error: "✗ failed", skipped: "not requested", disabled: "not enabled" };

const upper = (value) => (value || "").toString().toUpperCase();
const plural = (n, word) => `${n} ${word}${n === 1 ? "" : "s"}`;

// ---------- Entities and scoping ----------

/** The searched entities: how each input was resolved, plus header details looked up by the backend. */
export function queryEntities(query) {
  const normalisation = query.evidence?.normalisation || [];
  if (!normalisation.length) {
    return query.entities.map((name) => ({ input: name, name: upper(name), type: null, details: null }));
  }
  const details = query.entity_details || [];
  return normalisation.map((entry, i) => ({ ...entry, details: details[i] || null }));
}

/** Evidence restricted to one entity (or everything when focus is null). */
export function scopeEvidence(query, focus) {
  const evidence = query.evidence || {};
  const items = evidence.items || [];
  const table = evidence.table || [];
  const availability = evidence.australian_availability;
  if (!focus) return { items, table, availability, network: evidence.network };

  const name = focus.name;
  const listHas = (list) => (Array.isArray(list) ? list : String(list || "").split(/\s*,\s*/))
    .some((v) => upper(v) === name);
  const matches = (item) => {
    switch (item.kind) {
      case "variant_annotation": return listHas(item.genes) || listHas(item.drugs);
      case "publication": return upper(item.query).split(/\s+AND\s+/).includes(name);
      default: return upper(item.gene) === name || upper(item.drug) === name;
    }
  };
  const scopedItems = items.filter(matches);
  const ids = new Set(scopedItems.map((i) => i.id));
  const scopedTable = table.filter((r) => ids.has(r.evidence_id));
  const drugs = new Set([name, ...scopedTable.map((r) => upper(r.drug))]);
  return {
    items: scopedItems,
    table: scopedTable,
    availability: availability && availability.filter((a) => drugs.has(upper(a.drug))),
    network: scopeNetwork(evidence.network, name),
  };
}

/** The focused node, its neighbours, and the variant edges hanging off those neighbours. */
function scopeNetwork(network, name) {
  if (!network) return network;
  const focusIds = new Set(network.nodes.filter((n) => upper(n.data.label) === name).map((n) => n.data.id));
  const touches = (e, set) => set.has(e.data.source) || set.has(e.data.target);
  const direct = network.edges.filter((e) => touches(e, focusIds));
  const neighbours = new Set(direct.flatMap((e) => [e.data.source, e.data.target]));
  const variants = new Set(network.nodes.filter((n) => n.data.type === "variant" && neighbours.has(n.data.id))
    .map((n) => n.data.id));
  const edges = network.edges.filter((e) => touches(e, focusIds) || touches(e, variants));
  const used = new Set(edges.flatMap((e) => [e.data.source, e.data.target]));
  return { nodes: network.nodes.filter((n) => used.has(n.data.id) || focusIds.has(n.data.id)), edges };
}

// ---------- Headline numbers ----------

export function interactionRows(table) {
  return table.filter((r) => r.category !== "snp");
}

function bestBy(values, order) {
  return values.filter((v) => v in order).sort((a, b) => order[a] - order[b])[0] || null;
}

/** Numbers shown in the header and the section nav. Each links to the section that lists them. */
export function headlineNumbers(scoped, entities, focus) {
  const rows = interactionRows(scoped.table);
  const drugQuery = focus ? focus.type === "drug" : entities.every((e) => e.type === "drug");
  const partnerKey = drugQuery ? "gene" : "drug";
  const partners = new Set(rows.map((r) => upper(r[partnerKey])).filter(Boolean));
  for (const e of focus ? [focus] : entities) partners.delete(e.name); // count partners, not the searched entities
  const typed = rows.filter((r) => r.category !== "other").length;
  const guidelines = scoped.items.filter((i) => i.kind === "guideline");
  const annotations = scoped.items.filter((i) => i.kind === "variant_annotation");
  const papers = scoped.items.filter((i) => i.kind === "publication");
  const listed = (scoped.availability || []).filter((a) => a.pbs_listed).length;
  const bestCpic = bestBy(guidelines.map((g) => g.cpic_level), CPIC_LEVEL_ORDER);
  const bestPgkb = bestBy(annotations.map((a) => a.level), PGKB_LEVEL_ORDER);

  return {
    counts: {
      interactions: rows.length,
      pgx: guidelines.length + annotations.length,
      literature: papers.length,
      pbs: scoped.availability ? listed : null,
    },
    tiles: [
      { value: partners.size, label: drugQuery ? "Genes" : "Drugs", sub: `${rows.length} interactions`, href: "#sec-interactions" },
      { value: typed, label: "Known action", sub: "inhibit, induce, bind…", href: "#sec-interactions" },
      { value: guidelines.length, label: "CPIC guidelines", sub: bestCpic ? `best level ${bestCpic}` : "none", href: "#sec-pgx" },
      { value: annotations.length, label: "PGx annotations", sub: bestPgkb ? `top level ${bestPgkb}` : "PharmGKB", href: "#sec-pgx" },
      { value: papers.length, label: "Papers", sub: "PubTator", href: "#sec-literature" },
      scoped.availability
        ? { value: listed, label: "PBS-listed drugs", sub: `of ${scoped.availability.length} checked`, href: "#sec-pbs" }
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

// ---------- Entity header ----------

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
    d?.chembl_id && ["Open Targets", `https://platform.opentargets.org/drug/${d.chembl_id}`],
    ["PBS", `https://www.pbs.gov.au/pbs/search?term=${encodeURIComponent(name.toLowerCase())}`],
  ].filter(Boolean);
}

function describeEntity(entity) {
  const d = entity.details;
  if (entity.type === "gene") {
    const locus = d?.locus_type === "gene with protein product" ? "Protein-coding gene" : d?.locus_type;
    return {
      fullName: d?.full_name,
      meta: [d?.location && `Chr ${d.location}`, locus, entity.id, d?.ensembl_gene_id,
             d?.uniprot_ids?.length && `UniProt ${d.uniprot_ids.join(", ")}`],
      links: geneLinks(entity.name, d),
    };
  }
  if (entity.type === "drug") {
    const phase = d?.max_phase == null ? null : d.max_phase >= 4 ? "Approved" : `Max phase ${d.max_phase}`;
    return {
      fullName: d?.molecule_type,
      meta: [phase, d?.first_approval && `first approved ${d.first_approval}`, entity.id],
      links: drugLinks(entity.name, d),
    };
  }
  return { fullName: null, meta: [], links: [] }; // the interpretation note explains why it is unresolved
}

/** How inputs were interpreted when that isn't obvious (P53 → TP53, Plavix → CLOPIDOGREL), and lookup warnings. */
function interpretationNote(entities) {
  const shown = entities.filter((e) => !e.type || e.name !== upper(e.input).trim() || e.note);
  if (!shown.length) return null;
  return el("p", { class: "eh-note small" }, "Input interpreted as: ", shown.map((e, i) => [
    i ? " · " : "",
    e.type
      ? [`${e.input} → ${e.name}`, e.matched_by ? ` (${e.matched_by})` : "",
         e.note ? el("span", { class: "report-warning" }, `; ${e.note}`) : ""]
      : el("span", { class: "report-warning" }, `${e.input}: ${e.note || "not found in local reference data"}`),
  ]));
}

function sourcePills(status) {
  return el("div", { class: "source-status" },
    el("span", { class: "muted small" }, "Sources:"),
    Object.entries(SOURCE_NAMES).map(([key, name]) => {
      const s = status[key] || { state: "skipped" };
      const label = s.state === "ok" ? `${name} ✓ ${s.count}` : `${name} ${SOURCE_STATES[s.state] || s.state}`;
      return el("span", { class: `source-pill ${s.state}`, title: s.error || s.version || "" }, label);
    }));
}

export function renderHeader(container, { query, entities, focus, numbers, onFocus }) {
  const multiple = entities.length > 1;
  const switcher = multiple && el("div", { class: "entity-switch", role: "tablist", "aria-label": "Show results for" },
    el("button", { type: "button", role: "tab", class: focus ? "" : "active", "aria-selected": String(!focus),
                   onclick: () => onFocus(null) }, `All (${entities.length})`),
    entities.map((e) => el("button", {
      type: "button", role: "tab", class: focus?.name === e.name ? "active" : "",
      "aria-selected": String(focus?.name === e.name), onclick: () => onFocus(e),
    }, `${TYPE_ICONS[e.type] || "❔"} ${e.name}`)));

  let title, typeBadge, typeClass = "", fullName, meta, links;
  if (focus || !multiple) {
    const entity = focus || entities[0];
    ({ fullName, meta, links } = describeEntity(entity));
    title = entity.name;
    typeBadge = TYPE_LABELS[entity.type] || "Unresolved";
    typeClass = entity.type || "";
  } else {
    const genes = entities.filter((e) => e.type === "gene").length;
    const drugs = entities.filter((e) => e.type === "drug").length;
    const names = entities.map((e) => e.name);
    title = names.length > 4 ? `${names.slice(0, 4).join(" · ")} +${names.length - 4} more` : names.join(" · ");
    typeBadge = genes && !drugs ? "Gene panel" : "Multiple entities";
    fullName = [genes && plural(genes, "gene"), drugs && plural(drugs, "drug"),
                entities.length - genes - drugs && `${entities.length - genes - drugs} unresolved`]
      .filter(Boolean).join(", ") + ". Pick one above to focus every section on it.";
    meta = [];
    links = [];
  }

  // replaceChildren() would print a null as the text "null", so optional parts are filtered out.
  container.replaceChildren(...[
    el("div", { class: "eh-top" },
      switcher || el("span"),
      el("span", { class: "muted small" },
        `Query #${query.id}${query.parent_id ? ` (follow-up to #${query.parent_id})` : ""} · ${formatDate(query.created_at)}`)),
    el("div", { class: "eh-title" },
      el("h2", {}, title),
      el("span", { class: `type-badge ${typeClass}` }, typeBadge),
      links.length ? el("div", { class: "eh-links" }, links.map(([label, href]) =>
        el("a", { href, target: "_blank", rel: "noopener" }, `${label} ↗`))) : null),
    fullName ? el("p", { class: "eh-name" }, fullName) : null,
    meta.filter(Boolean).length ? el("p", { class: "eh-meta small" }, meta.filter(Boolean).join(" · ")) : null,
    interpretationNote(focus ? [focus] : entities),
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
