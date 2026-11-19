/** Tiny DOM helper: el("a", { href, class: "x", onclick }, "text", childNode) */
export function el(tag, attrs = {}, ...children) {
  const node = document.createElement(tag);
  for (const [key, value] of Object.entries(attrs)) {
    if (value == null || value === false) continue;
    if (key.startsWith("on") && typeof value === "function") node.addEventListener(key.slice(2), value);
    else if (key === "html") node.innerHTML = value; // only pass sanitised HTML here
    else node.setAttribute(key, value === true ? "" : value);
  }
  for (const child of children.flat(Infinity)) {
    if (child == null || child === false) continue;
    node.append(child instanceof Node ? child : String(child));
  }
  return node;
}

export const CATEGORY_LABELS = {
  inhibit: "Inhibition",
  induce: "Induction / activation",
  bind: "Binding",
  metabolize: "Metabolism",
  snp: "SNP-specific response",
  other: "Unspecified",
  variant_of: "Variant of",
  sensitive: "Response (CIViC / OncoKB)",
  resistant: "Resistance",
  gene_link: "Gene-level link (DGIdb / ChEMBL)",
};

/** Stage 4 evidence grades, strongest first (see backend services/workflow/evidence.py). */
export const GRADES = {
  strong: "Strong",
  moderate: "Moderate",
  supportive: "Supportive",
  limited: "Limited",
};

export function categoryColor(key) {
  const value = getComputedStyle(document.documentElement).getPropertyValue(`--cat-${key}`).trim();
  return value || "#8a94a3";
}

export function cssVar(name) {
  return getComputedStyle(document.documentElement).getPropertyValue(name).trim();
}

export function formatDate(iso) {
  return iso ? new Date(iso).toLocaleString("en-AU") : "";
}

export function describeEvidence(item) {
  switch (item.kind) {
    case "interaction":
      return `DGIdb · ${item.gene} – ${item.drug} (${item.interaction_types.join(", ") || "unspecified"}; score ${item.score ?? "n/a"})`;
    case "mechanism":
      return `ChEMBL · ${item.drug} – ${item.gene} (${item.action_type || "unspecified"}; ${item.mechanism})`;
    case "guideline":
      return `CPIC · ${item.gene} – ${item.drug} (level ${item.cpic_level}${item.guideline_name ? `; ${item.guideline_name}` : ""})`;
    case "variant_annotation":
      return `PharmGKB · ${item.variant} – ${item.drugs.join(", ")} (Level ${item.level}; ${item.phenotype_categories.join(", ")})`;
    case "publication":
      return `PubTator · ${item.title} ${item.journal || ""} (${item.year || "n.d."}). PMID ${item.pmid}`;
    case "drug_info":
      return `DrugBank · ${item.drug} (${item.drugbank_id || ""})`;
    case "variant":
      return `Patient variant · ${item.notation}${item.hgvsc ? ` (${item.hgvsc})` : ""}`;
    case "civic":
      return `CIViC EID${item.evidence_id} · ${item.molecular_profile} – ${item.therapies.join(", ")} (${item.significance}; level ${item.level}; ${item.disease})`;
    case "genotype":
      return `CPIC · ${item.genotype} → ${item.phenotype}: ${item.drug} (${item.classification || "n/a"}${(item.population_label ?? (item.population === "general" ? "" : item.population)) ? `; ${item.population_label ?? item.population}` : ""})`;
    case "oncokb":
      return `OncoKB · ${item.gene} ${item.protein_change} – ${item.drug} (${item.level})`;
    case "profile":
      return `${item.source} · ${item.entity} gene profile${item.protein?.protein_name ? ` (${item.protein.protein_name})` : ""}`;
    default:
      return `${item.source} · ${item.id}`;
  }
}

/** Broadcast "the user is looking at evidence Ex" so table, network and reports can react. */
export function focusEvidence(eid) {
  document.dispatchEvent(new CustomEvent("evidence:focus", { detail: eid }));
}

/**
 * Copy each table's column headers onto its body cells (data-label) and mark the table "stacks", so on
 * phones the CSS can show every row as a card of "header: value" lines instead of a squeezed grid.
 * Watches root for tables that are rendered or re-rendered later (sorting, filtering, new results).
 */
export function labelTableCells(root = document.body) {
  const label = (table) => {
    const heads = [...table.querySelectorAll(":scope > thead > tr:last-child > th")].map((th) => th.textContent.trim());
    if (!heads.length) return;
    table.classList.add("stacks");
    for (const row of table.querySelectorAll(":scope > tbody > tr")) {
      let col = 0;
      for (const cell of row.cells) {
        if (cell.colSpan === 1 && heads[col]) cell.dataset.label = heads[col];
        col += cell.colSpan;
      }
    }
  };
  root.querySelectorAll("table").forEach(label);
  new MutationObserver((mutations) => {
    const tables = new Set();
    for (const m of mutations) {
      for (const node of m.addedNodes) {
        if (node.nodeType !== Node.ELEMENT_NODE) continue;
        const own = node.closest("table");
        if (own) tables.add(own);
        node.querySelectorAll("table").forEach((t) => tables.add(t));
      }
    }
    tables.forEach(label);
  }).observe(root, { childList: true, subtree: true });
}

/**
 * Guard against overlapping requests showing stale data: call the returned function when a request starts; it
 * gives back isLatest(), which is false once a newer request has started (e.g. the user clicked another row).
 *   const logRequest = latestOnly();
 *   const isLatest = logRequest(); const data = await api.x(); if (!isLatest()) return;
 */
export function latestOnly() {
  let latest = 0;
  return () => {
    const ticket = ++latest;
    return () => ticket === latest;
  };
}

/** "Copy link": the ?q= address of the current result. */
export async function copyLink(status) {
  const url = `${location.origin}${location.pathname}${location.search}`;
  try {
    await navigator.clipboard.writeText(url);
    status.textContent = "Link copied";
  } catch {
    status.textContent = url; // clipboard blocked (e.g. not https): show it to copy by hand
  }
}
