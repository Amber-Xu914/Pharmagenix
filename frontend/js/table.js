import { CATEGORY_LABELS, GRADES, categoryColor, el, focusEvidence } from "./util.js";
import { gradeBadge } from "./workflow.js";

// Rows with an unspecified interaction type (about 90% of DGIdb) are collapsed to this many until expanded.
const UNSPECIFIED_PREVIEW = 5;
const GRADE_ORDER = Object.keys(GRADES);

const phaseOf = (row) => Number(/Phase (\d+(?:\.\d+)?)/.exec(row.level || "")?.[1] ?? -1);
const gradeRank = (row) => (row.grade ? GRADE_ORDER.indexOf(row.grade) : GRADE_ORDER.length);
const isUnspecified = (row) => row.categories.length === 1 && row.categories[0] === "other";

/** Rows are one graded gene–drug pair each (stage 5). Rows saved before that had one row per evidence item. */
function normaliseRow(row) {
  return {
    ...row,
    evidence_ids: row.evidence_ids || [row.evidence_id],
    categories: row.categories || [row.category],
    sources: row.sources || [],
    pmids: row.pmids || [],
    conflicts: row.conflicts || [],
  };
}

/** By evidence grade, then typed before unspecified, phase, DGIdb score. */
function defaultOrder(a, b) {
  return gradeRank(a) - gradeRank(b)
    || isUnspecified(a) - isUnspecified(b)
    || phaseOf(b) - phaseOf(a)
    || (b.score ?? -1) - (a.score ?? -1);
}

const COLUMNS = [
  { key: "gene", label: "Gene" },
  { key: "drug", label: "Drug" },
  { key: "category", label: "Relationship" },
  { key: "relation", label: "Action / mechanism" },
  { key: "grade", label: "Evidence", sort: (r) => -gradeRank(r) * 1000 + (r.score ?? 0) },
  { key: "sources", label: "Sources", sort: (r) => r.sources.length },
  { key: "pbs", label: "PBS" },
  { key: "evidence_id", label: "Refs", sort: (r) => Number(r.evidence_ids[0].slice(1)) },
];

export class DgiTable {
  constructor({ table, filterInput, categorySelect, countEl, exportButton, toggleButton }) {
    Object.assign(this, { table, filterInput, categorySelect, countEl, toggleButton });
    this.rows = [];
    this.items = new Map();
    this.pbs = new Map();
    this.sortKey = null;
    this.sortDir = 1;
    this.showAll = false;
    filterInput.addEventListener("input", () => this.render());
    categorySelect.addEventListener("change", () => this.render());
    exportButton.addEventListener("click", () => this.exportCsv());
    toggleButton.addEventListener("click", () => {
      this.showAll = !this.showAll;
      this.render();
    });
  }

  /** rows: table rows for interactions/mechanisms; items: evidence by ID; availability: PBS entries. */
  setRows(rows, { items = new Map(), availability = null } = {}) {
    this.rows = (rows || []).map((r) => ({ ...normaliseRow(r), pbs: pbsStatus(availability, r.drug) }));
    this.items = items;
    this.sortKey = null;
    this.showAll = false;
    const categories = [...new Set(this.rows.flatMap((r) => r.categories))];
    const current = this.categorySelect.value;
    this.categorySelect.replaceChildren(
      el("option", { value: "" }, "All relationship types"),
      ...categories.map((c) => el("option", { value: c }, CATEGORY_LABELS[c] || c)));
    this.categorySelect.value = categories.includes(current) ? current : "";
    this.render();
  }

  /** Rows matching the filters, sorted; ignores the unspecified-row collapse (used for CSV export too). */
  filteredRows() {
    const text = this.filterInput.value.trim().toLowerCase();
    const category = this.categorySelect.value;
    const rows = this.rows.filter((r) =>
      (!category || r.categories.includes(category))
      && (!text || [r.gene, r.drug, r.relation].some((v) => v && v.toLowerCase().includes(text))));
    if (!this.sortKey) return rows.sort(defaultOrder);
    const column = COLUMNS.find((c) => c.key === this.sortKey);
    const value = column.sort || ((r) => (r[column.key] ?? "").toString().toLowerCase());
    return rows.sort((a, b) => (value(a) > value(b) ? 1 : value(a) < value(b) ? -1 : 0) * this.sortDir);
  }

  /** Collapse unspecified rows unless the user expanded them or is filtering. */
  visibleRows(rows, showAll = this.showAll) {
    const filtering = this.filterInput.value.trim() || this.categorySelect.value;
    if (showAll || filtering) return { rows, hidden: 0 };
    const unspecified = rows.filter(isUnspecified);
    const keep = new Set(unspecified.slice(0, UNSPECIFIED_PREVIEW));
    const shown = rows.filter((r) => !isUnspecified(r) || keep.has(r));
    return { rows: shown, hidden: rows.length - shown.length };
  }

  render() {
    const filtered = this.filteredRows();
    const { rows, hidden } = this.visibleRows(filtered);
    const head = el("tr", {}, COLUMNS.map((c) => {
      const th = el("th", { "data-sort": c.key, class: this.sortKey === c.key ? (this.sortDir > 0 ? "asc" : "desc") : null }, c.label);
      th.addEventListener("click", () => {
        this.sortDir = this.sortKey === c.key ? -this.sortDir : 1;
        this.sortKey = c.key;
        this.render();
      });
      return th;
    }));
    const body = rows.length
      ? rows.map((r) => this.renderRow(r))
      : [el("tr", {}, el("td", { colspan: COLUMNS.length, class: "muted" }, "No interactions match."))];
    this.table.replaceChildren(el("thead", {}, head), el("tbody", {}, body));
    this.countEl.textContent = `${rows.length} / ${this.rows.length} rows`;

    const collapsible = this.visibleRows(filtered, false).hidden > 0;
    this.toggleButton.classList.toggle("hidden", !collapsible);
    this.toggleButton.textContent = this.showAll
      ? "Show fewer interactions with unspecified type"
      : `▸ Show ${hidden} more interactions with unspecified type`;
  }

  renderRow(r) {
    return el("tr", { class: "ev-row", "data-eids": r.evidence_ids.join(" ") },
      el("td", {}, r.gene),
      el("td", {}, r.drug),
      el("td", {}, r.categories.map((c) => el("span", { class: "cat-badge", style: `background:${categoryColor(c)}` },
        CATEGORY_LABELS[c] || c))),
      el("td", {}, r.url ? el("a", { href: r.url, target: "_blank", rel: "noopener" }, r.relation) : r.relation),
      el("td", { class: "evidence-cell" }, evidenceBadges(r, this.items.get(r.evidence_ids[0]) || {})),
      el("td", { title: r.sources.join(", ") }, r.sources.length > 2 ? `${r.sources.slice(0, 2).join(", ")} +${r.sources.length - 2}` : r.sources.join(", ")),
      el("td", {}, r.pbs === "listed" ? el("span", { class: "badge success" }, "Listed")
        : r.pbs === "not listed" ? el("span", { class: "muted small" }, "Not listed") : ""),
      el("td", { class: "ref-cell" },
        r.evidence_ids.slice(0, 3).map((eid, i) => [i ? ", " : "",
          el("a", { href: "#", onclick: (e) => { e.preventDefault(); focusEvidence(eid); } }, eid)]),
        r.evidence_ids.length > 3 ? el("span", { class: "muted small", title: r.evidence_ids.join(", ") }, ` +${r.evidence_ids.length - 3}`) : "",
        r.pmids.length ? el("a", {
          href: `https://pubmed.ncbi.nlm.nih.gov/?term=${r.pmids.slice(0, 20).join("+OR+")}`,
          target: "_blank", rel: "noopener", class: "muted small pmids", title: r.pmids.slice(0, 20).join(", "),
        }, `${r.pmids.length} PMID${r.pmids.length > 1 ? "s" : ""}`) : ""),
    );
  }

  /** Highlight the row holding an evidence ID, expanding the collapse or clearing filters if they hide it. */
  highlight(eid) {
    if (!this.rows.some((r) => r.evidence_ids.includes(eid))) return false;
    const selector = `tr[data-eids~="${CSS.escape(eid)}"]`;
    let row = this.table.querySelector(selector);
    if (!row) {
      this.showAll = true;
      this.filterInput.value = "";
      this.categorySelect.value = "";
      this.render();
      row = this.table.querySelector(selector);
    }
    return row;
  }

  exportCsv() {
    const quote = (v) => `"${String(v ?? "").replaceAll('"', '""')}"`;
    const keys = ["gene", "drug", "categories", "relation", "grade", "grade_reason", "level", "score", "conflicts",
                  "sources", "databases", "pmids", "pbs", "evidence_ids"];
    const lines = [keys.map(quote).join(",")];
    for (const r of this.filteredRows()) {
      lines.push(keys.map((k) => quote(Array.isArray(r[k]) ? r[k].join("; ") : r[k])).join(","));
    }
    const blob = new Blob(["﻿" + lines.join("\n")], { type: "text/csv;charset=utf-8" });
    const link = el("a", { href: URL.createObjectURL(blob), download: "dgi_interactions.csv" });
    link.click();
    URL.revokeObjectURL(link.href);
  }
}

function pbsStatus(availability, drug) {
  if (!availability || !drug) return null;
  const entry = availability.find((a) => a.drug.toUpperCase() === drug.toUpperCase());
  return entry ? (entry.pbs_listed ? "listed" : "not listed") : null;
}

/** The stage 5 grade (with its reason), then the markers behind it: clinical levels, phase, DGIdb score, conflicts. */
function evidenceBadges(row, item) {
  const badges = [gradeBadge(row.grade, row.grade_reason)];
  if (row.level) badges.push(el("span", { class: "ev-badge", title: "CPIC / PharmGKB level, ChEMBL clinical phase" }, row.level));
  else if (item.kind === "mechanism") badges.push(el("span", { class: "ev-badge curated" }, "ChEMBL curated"));
  if (row.score != null && row.score > 0) badges.push(el("span", { class: "ev-badge", title: "DGIdb interaction score" }, `DGIdb ${row.score}`));
  if (row.drug_approved || item.drug_approved || phaseOf(row) >= 4) badges.push(el("span", { class: "ev-badge approved",
    title: "Approved for at least one indication (DGIdb / ChEMBL max phase 4); not necessarily for this gene or disease" }, "Approved"));
  if (row.conflicts.length) badges.push(el("span", { class: "ev-badge conflict", title: row.conflicts.join(" ") }, "⚠ Conflicting"));
  return badges;
}
