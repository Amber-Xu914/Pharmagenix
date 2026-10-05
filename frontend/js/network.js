import { CATEGORY_LABELS, categoryColor, cssVar, el, focusEvidence } from "./util.js";

const NODE_COLORS = { gene: "#2563eb", drug: "#f08c00", variant: "#8e44ad" };
const NODE_LABELS = { gene: "Gene", drug: "Drug", variant: "Variant" };
const EDGE_CATEGORIES = ["inhibit", "induce", "bind", "metabolize", "other", "snp", "variant_of"];
// Arrow drawn at the gene end: drug ⊣ gene (inhibit), drug → gene (induce), drug ● gene (bind)
const ARROWS = { inhibit: "tee", induce: "triangle", bind: "circle", metabolize: "square" };

export class NetworkView {
  constructor({ container, info, legend }) {
    Object.assign(this, { container, info, legend });
    this.cy = null;
    this.renderLegend();
  }

  renderLegend() {
    this.legend.replaceChildren(
      ...Object.entries(NODE_COLORS).map(([type, color]) =>
        el("span", {}, el("span", { class: "shape", style: `background:${color};border-radius:${type === "drug" ? "50%" : "3px"}` }), NODE_LABELS[type])),
      ...EDGE_CATEGORIES.map((cat) =>
        el("span", {}, el("span", { class: "line", style: `background:${categoryColor(cat)}` }), CATEGORY_LABELS[cat])),
    );
  }

  render(network, edgeLimit = 150) {
    const { nodes, edges } = selectElements(network || { nodes: [], edges: [] }, edgeLimit);
    this.cy?.destroy();
    this.cy = cytoscape({
      container: this.container,
      elements: { nodes, edges },
      style: this.style(),
      layout: { name: "cose", animate: false, nodeRepulsion: 9000, idealEdgeLength: 90, randomize: true },
      wheelSensitivity: 0.3,
      minZoom: 0.15,
      maxZoom: 3,
    });
    this.cy.on("tap", "node", (e) => this.showNode(e.target));
    this.cy.on("tap", "edge", (e) => this.showEdge(e.target));
    this.cy.on("tap", (e) => { if (e.target === this.cy) this.clearFocus(); });
    const hidden = (network?.edges.length || 0) - edges.length;
    this.info.textContent = hidden > 0
      ? `Showing ${edges.length} relationships (${hidden} more hidden; change this above). Select a node or edge to view details.`
      : "Select a node or edge to view details.";
  }

  style() {
    const text = cssVar("--text") || "#1c2430";
    const bg = cssVar("--surface") || "#fff";
    return [
      { selector: "node", style: {
        label: "data(label)", "font-size": 10, color: text, "text-valign": "bottom", "text-margin-y": 4,
        "text-outline-color": bg, "text-outline-width": 2, width: 22, height: 22,
      } },
      ...Object.entries(NODE_COLORS).map(([type, color]) => ({
        selector: `node[type = "${type}"]`,
        style: {
          "background-color": color,
          shape: type === "gene" ? "round-rectangle" : type === "variant" ? "diamond" : "ellipse",
        },
      })),
      { selector: "node[?is_query]", style: { width: 36, height: 36, "border-width": 3, "border-color": text, "font-size": 13, "font-weight": 700 } },
      { selector: "edge", style: { width: 2, "curve-style": "bezier", opacity: 0.8, "arrow-scale": 1.1 } },
      ...EDGE_CATEGORIES.map((cat) => ({
        selector: `edge[category = "${cat}"]`,
        style: {
          "line-color": categoryColor(cat),
          "source-arrow-color": categoryColor(cat),
          "source-arrow-shape": ARROWS[cat] || "none",
          "line-style": cat === "snp" ? "dashed" : cat === "variant_of" ? "dotted" : "solid",
        },
      })),
      { selector: ".faded", style: { opacity: 0.12 } },
      { selector: ".focused", style: { width: 5, opacity: 1, "z-index": 10 } },
    ];
  }

  fit() {
    this.cy?.fit(undefined, 30);
  }

  showNode(node) {
    const neighbours = node.neighborhood().add(node);
    this.cy.elements().addClass("faded");
    neighbours.removeClass("faded");
    const type = node.data("type");
    this.info.replaceChildren(`${NODE_LABELS[type] || type} ${node.data("label")} · ${node.connectedEdges().length} relationships`);
  }

  showEdge(edge) {
    this.cy.elements().addClass("faded");
    edge.union(edge.connectedNodes()).removeClass("faded").addClass("focused");
    const d = edge.data();
    const ids = d.evidence_ids.map((eid, i) => [
      i ? ", " : "",
      el("a", { href: "#", onclick: (e) => { e.preventDefault(); focusEvidence(eid); } }, eid),
    ]);
    this.info.replaceChildren(
      `${edge.source().data("label")} — ${edge.target().data("label")} · ${CATEGORY_LABELS[d.category] || d.category}`,
      d.label ? ` (${d.label})` : "", " · evidence ", ...ids.flat());
  }

  clearFocus() {
    this.cy?.elements().removeClass("faded focused");
  }

  /** Returns false when the evidence isn't drawn (e.g. hidden by the edge limit). */
  focusEvidence(eid) {
    if (!this.cy) return false;
    const edges = this.cy.edges().filter((e) => e.data("evidence_ids").includes(eid));
    if (!edges.length) return false;
    this.clearFocus();
    this.cy.elements().addClass("faded");
    edges.union(edges.connectedNodes()).removeClass("faded");
    edges.addClass("focused");
    this.cy.animate({ center: { eles: edges }, duration: 300 });
    return true;
  }
}

/** Keep the graph readable: top interaction edges by score plus a share of variant edges. */
function selectElements(network, limit) {
  let edges = network.edges;
  if (limit > 0 && edges.length > limit) {
    const variantEdges = edges.filter((e) => ["snp", "variant_of"].includes(e.data.category));
    const interactionEdges = edges.filter((e) => !["snp", "variant_of"].includes(e.data.category));
    const variantShare = Math.min(variantEdges.length, Math.floor(limit / 3));
    edges = [...interactionEdges.slice(0, limit - variantShare), ...variantEdges.slice(0, variantShare)];
  }
  const used = new Set(edges.flatMap((e) => [e.data.source, e.data.target]));
  const nodes = network.nodes.filter((n) => used.has(n.data.id) || n.data.is_query);
  return { nodes, edges };
}
