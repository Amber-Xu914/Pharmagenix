import { CATEGORY_LABELS, categoryColor, el, focusEvidence } from "./util.js";

const LEVEL_ORDER = { "1A": 0, "1B": 1, "2A": 2, "2B": 3, "3": 4, "4": 5 };

const COLUMNS = [
  { key: "evidence_id", label: "Evidence", sort: (r) => Number(r.evidence_id.slice(1)) },
  { key: "gene", label: "Gene" },
  { key: "drug", label: "Drug" },
  { key: "variant", label: "Variant" },
  { key: "category", label: "Relationship type" },
  { key: "relation", label: "Action / phenotype" },
  { key: "score", label: "Score", sort: (r) => r.score ?? -1 },
  { key: "level", label: "Evidence level", sort: (r) => LEVEL_ORDER[r.level] ?? 99 },
  { key: "sources", label: "Sources", sort: (r) => r.sources.join(",") },
  { key: "pmids", label: "PMID", sort: (r) => r.pmids.length },
];

export class DgiTable {
  constructor({ table, filterInput, categorySelect, countEl, exportButton }) {
    Object.assign(this, { table, filterInput, categorySelect, countEl });
    this.rows = [];
    this.sortKey = null;
    this.sortDir = 1;
    filterInput.addEventListener("input", () => this.render());
    categorySelect.addEventListener("change", () => this.render());
    exportButton.addEventListener("click", () => this.exportCsv());
  }

  setRows(rows) {
    this.rows = rows || [];
    this.sortKey = null;
    const categories = [...new Set(this.rows.map((r) => r.category))];
    this.categorySelect.replaceChildren(
      el("option", { value: "" }, "All relationship types"),
      ...categories.map((c) => el("option", { value: c }, CATEGORY_LABELS[c] || c)));
    this.render();
  }

  visibleRows() {
    const text = this.filterInput.value.trim().toLowerCase();
    const category = this.categorySelect.value;
    let rows = this.rows.filter((r) =>
      (!category || r.category === category)
      && (!text || [r.gene, r.drug, r.variant, r.relation].some((v) => v && v.toLowerCase().includes(text))));
    if (this.sortKey) {
      const column = COLUMNS.find((c) => c.key === this.sortKey);
      const value = column.sort || ((r) => (r[column.key] ?? "").toString().toLowerCase());
      rows = [...rows].sort((a, b) => (value(a) > value(b) ? 1 : value(a) < value(b) ? -1 : 0) * this.sortDir);
    }
    return rows;
  }

  render() {
    const rows = this.visibleRows();
    const head = el("tr", {}, COLUMNS.map((c) => {
      const th = el("th", { "data-sort": c.key, class: this.sortKey === c.key ? (this.sortDir > 0 ? "asc" : "desc") : null }, c.label);
      th.addEventListener("click", () => {
        this.sortDir = this.sortKey === c.key ? -this.sortDir : 1;
        this.sortKey = c.key;
        this.render();
      });
      return th;
    }));
    const body = rows.map((r) => el("tr", { "data-eid": r.evidence_id },
      el("td", {}, el("a", { href: "#", onclick: (e) => { e.preventDefault(); focusEvidence(r.evidence_id); } }, r.evidence_id)),
      el("td", {}, r.gene),
      el("td", {}, r.drug),
      el("td", {}, r.variant || ""),
      el("td", {}, el("span", { class: "cat-badge", style: `background:${categoryColor(r.category)}` },
        CATEGORY_LABELS[r.category] || r.category)),
      el("td", {}, r.url ? el("a", { href: r.url, target: "_blank", rel: "noopener" }, r.relation) : r.relation),
      el("td", {}, r.score ?? ""),
      el("td", {}, r.level || ""),
      el("td", {}, r.sources.join(", ")),
      el("td", {}, r.pmids.slice(0, 5).flatMap((pmid, i) => [
        i ? ", " : "",
        el("a", { href: `https://pubmed.ncbi.nlm.nih.gov/${pmid}/`, target: "_blank", rel: "noopener" }, pmid),
      ])),
    ));
    this.table.replaceChildren(el("thead", {}, head), el("tbody", {}, body));
    this.countEl.textContent = `${rows.length} / ${this.rows.length} rows`;
  }

  highlight(eid) {
    this.table.querySelectorAll("tr.highlight").forEach((tr) => tr.classList.remove("highlight"));
    const row = this.table.querySelector(`tr[data-eid="${CSS.escape(eid)}"]`);
    if (row) {
      row.classList.add("highlight");
      row.scrollIntoView({ block: "nearest", behavior: "smooth" });
    }
  }

  exportCsv() {
    const quote = (v) => `"${String(v ?? "").replaceAll('"', '""')}"`;
    const lines = [COLUMNS.map((c) => quote(c.key)).join(",")];
    for (const r of this.visibleRows()) {
      lines.push(COLUMNS.map((c) => quote(Array.isArray(r[c.key]) ? r[c.key].join("; ") : r[c.key])).join(","));
    }
    const blob = new Blob(["﻿" + lines.join("\n")], { type: "text/csv;charset=utf-8" });
    const link = el("a", { href: URL.createObjectURL(blob), download: "dgi_interactions.csv" });
    link.click();
    URL.revokeObjectURL(link.href);
  }
}
