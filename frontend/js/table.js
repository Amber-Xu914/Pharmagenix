import { CATEGORY_LABELS, categoryColor, el, focusEvidence } from "./util.js";

// Rows with an unspecified interaction type (about 90% of DGIdb) are collapsed to this many until expanded.
const UNSPECIFIED_PREVIEW = 5;

const phaseOf = (row) => Number(/Phase (\d+(?:\.\d+)?)/.exec(row.level || "")?.[1] ?? -1);

/** Typed interactions first, then curated (ChEMBL) mechanisms by clinical phase, then DGIdb score. */
function defaultOrder(a, b) {
  return (a.category === "other") - (b.category === "other")
    || phaseOf(b) - phaseOf(a)
    || (b.score ?? -1) - (a.score ?? -1);
}

const COLUMNS = [
  { key: "gene", label: "Gene" },
  { key: "drug", label: "Drug" },
  { key: "category", label: "Relationship" },
  { key: "relation", label: "Action / mechanism" },
  { key: "evidence", label: "Evidence", sort: (r) => phaseOf(r) * 1000 + (r.score ?? 0) },
  { key: "sources", label: "Sources", sort: (r) => r.sources.length },
  { key: "pbs", label: "PBS" },
  { key: "evidence_id", label: "Ref", sort: (r) => Number(r.evidence_id.slice(1)) },
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
    this.rows = (rows || []).map((r) => ({ ...r, pbs: pbsStatus(availability, r.drug) }));
    this.items = items;
    this.sortKey = null;
    this.showAll = false;
    const categories = [...new Set(this.rows.map((r) => r.category))];
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
      (!category || r.category === category)
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
    const unspecified = rows.filter((r) => r.category === "other");
    const keep = new Set(unspecified.slice(0, UNSPECIFIED_PREVIEW));
    const shown = rows.filter((r) => r.category !== "other" || keep.has(r));
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
    const item = this.items.get(r.evidence_id) || {};
    return el("tr", { class: "ev-row", "data-eid": r.evidence_id },
      el("td", {}, r.gene),
      el("td", {}, r.drug),
      el("td", {}, el("span", { class: "cat-badge", style: `background:${categoryColor(r.category)}` },
        CATEGORY_LABELS[r.category] || r.category)),
      el("td", {}, r.url ? el("a", { href: r.url, target: "_blank", rel: "noopener" }, r.relation) : r.relation),
      el("td", { class: "evidence-cell" }, evidenceBadges(r, item)),
      el("td", { title: r.sources.join(", ") }, r.sources.length > 2 ? `${r.sources.slice(0, 2).join(", ")} +${r.sources.length - 2}` : r.sources.join(", ")),
      el("td", {}, r.pbs === "listed" ? el("span", { class: "badge success" }, "Listed")
        : r.pbs === "not listed" ? el("span", { class: "muted small" }, "Not listed") : ""),
      el("td", { class: "ref-cell" },
        el("a", { href: "#", onclick: (e) => { e.preventDefault(); focusEvidence(r.evidence_id); } }, r.evidence_id),
        r.pmids.length ? el("a", {
          href: `https://pubmed.ncbi.nlm.nih.gov/?term=${r.pmids.slice(0, 20).join("+OR+")}`,
          target: "_blank", rel: "noopener", class: "muted small", title: r.pmids.slice(0, 20).join(", "),
        }, ` ${r.pmids.length} PMID${r.pmids.length > 1 ? "s" : ""}`) : ""),
    );
  }

  /** Highlight a row, expanding the collapse or clearing filters if they hide it. Returns false if absent. */
  highlight(eid) {
    if (!this.rows.some((r) => r.evidence_id === eid)) return false;
    let row = this.table.querySelector(`tr[data-eid="${CSS.escape(eid)}"]`);
    if (!row) {
      this.showAll = true;
      this.filterInput.value = "";
      this.categorySelect.value = "";
      this.render();
      row = this.table.querySelector(`tr[data-eid="${CSS.escape(eid)}"]`);
    }
    return row;
  }

  exportCsv() {
    const quote = (v) => `"${String(v ?? "").replaceAll('"', '""')}"`;
    const keys = ["evidence_id", "gene", "drug", "category", "relation", "score", "level", "sources", "pmids", "pbs"];
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

/** Real evidence markers only: ChEMBL clinical phase, DGIdb score and approval status. */
function evidenceBadges(row, item) {
  const badges = [];
  if (item.kind === "mechanism") {
    const phase = phaseOf(row);
    badges.push(el("span", { class: "ev-badge curated", title: "Curated mechanism of action (ChEMBL)" }, "ChEMBL curated"));
    if (phase >= 4) badges.push(el("span", { class: "ev-badge approved" }, "Approved"));
    else if (phase >= 0) badges.push(el("span", { class: "ev-badge" }, `Phase ${phase}`));
  } else {
    if (row.score != null) badges.push(el("span", { class: "ev-badge", title: "DGIdb interaction score" }, `DGIdb ${row.score}`));
    if (item.drug_approved) badges.push(el("span", { class: "ev-badge approved" }, "Approved"));
  }
  return badges;
}
