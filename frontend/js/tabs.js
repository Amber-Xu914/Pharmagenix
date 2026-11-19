/**
 * Result tabs: "Clinical" (what a clinic visit needs), "Extra" (the full evidence) and "Export" (downloads).
 * Each results <main> holds a .result-tabs bar of buttons [data-tab] and sections / nav links marked [data-in];
 * only the active tab's elements are shown. Following a link or citation to an element inside another tab
 * switches to that tab first. Fires "tab:change" ({ main, tab }) so hidden canvases (Cytoscape) can be drawn.
 */

export const TABS = ["clinical", "extra", "export"];

export function setTab(main, tab) {
  if (!main || !TABS.includes(tab)) return;
  const changed = main.dataset.tab !== tab;
  main.dataset.tab = tab;
  main.querySelectorAll("[data-in]").forEach((node) => { node.hidden = node.dataset.in !== tab; });
  main.querySelectorAll(".result-tabs [data-tab]").forEach((button) => {
    button.classList.toggle("active", button.dataset.tab === tab);
    button.setAttribute("aria-selected", String(button.dataset.tab === tab));
  });
  if (changed) document.dispatchEvent(new CustomEvent("tab:change", { detail: { main, tab } }));
}

/** Show the tab that holds node (no-op for nodes outside any tab). Returns node. */
export function revealTab(node) {
  const owner = node?.closest?.("[data-in]");
  const main = node?.closest?.("main.results");
  if (owner && main) setTab(main, owner.dataset.in);
  return node;
}

export function initTabs() {
  document.querySelectorAll("main.results").forEach((main) => {
    main.querySelectorAll(".result-tabs [data-tab]").forEach((button) => button.addEventListener("click", () => {
      setTab(main, button.dataset.tab);
      main.querySelector(".result-tabs").scrollIntoView({ block: "nearest" });
    }));
    setTab(main, "clinical");
  });
  // In-page links (KPI tiles, section nav, "see Extra" notes): open the target's tab, then let the browser scroll.
  document.addEventListener("click", (event) => {
    const link = event.target.closest?.('a[href^="#"]');
    const id = link?.getAttribute("href").slice(1);
    if (id) revealTab(document.getElementById(id));
  }, true);
}
