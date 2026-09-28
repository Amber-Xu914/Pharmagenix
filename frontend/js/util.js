/** Tiny DOM helper: el("a", { href, class: "x", onclick }, "text", childNode) */
export function el(tag, attrs = {}, ...children) {
  const node = document.createElement(tag);
  for (const [key, value] of Object.entries(attrs)) {
    if (value == null || value === false) continue;
    if (key.startsWith("on") && typeof value === "function") node.addEventListener(key.slice(2), value);
    else if (key === "html") node.innerHTML = value; // only pass sanitised HTML here
    else node.setAttribute(key, value === true ? "" : value);
  }
  for (const child of children.flat()) {
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
    default:
      return `${item.source} · ${item.id}`;
  }
}

/** Broadcast "the user is looking at evidence Ex" so table, network and reports can react. */
export function focusEvidence(eid) {
  document.dispatchEvent(new CustomEvent("evidence:focus", { detail: eid }));
}
