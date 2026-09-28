import { el } from "./util.js";

/** Australian PBS listing for the queried drug(s) and the most relevant related drugs. */
export function renderAvailability(container, entries) {
  if (!entries) {
    container.replaceChildren(el("p", { class: "muted" }, "PBS data has not been imported. Run: flask --app run import-data pbs"));
    return;
  }
  if (!entries.length) {
    container.replaceChildren(el("p", { class: "muted" }, "No drugs in this result to check against the PBS."));
    return;
  }
  const effective = entries.find((e) => e.effective_date)?.effective_date;
  const rows = entries.map((entry) => el("tr", {},
    el("td", {}, entry.drug),
    el("td", {}, entry.pbs_listed
      ? el("span", { class: "badge success" }, `Listed (${entry.item_count} item${entry.item_count > 1 ? "s" : ""})`)
      : el("span", { class: "badge" }, "Not listed")),
    el("td", {}, entry.items.map((item) => el("div", { class: "small" },
      el("a", { href: item.url, target: "_blank", rel: "noopener" }, item.pbs_code),
      ` · ${item.form} · ${item.benefit_type}`,
      item.brands.length ? el("span", { class: "muted" }, ` · ${item.brands.slice(0, 3).join(", ")}`) : ""))),
  ));
  container.replaceChildren(
    el("p", { class: "muted small" },
      `PBS Schedule${effective ? ` effective ${effective}` : ""}. Listing status only: check the PBS website for full `
      + "restriction criteria. Matching is by generic name, so combination products may be missed."),
    el("div", { class: "table-wrap" }, el("table", {},
      el("thead", {}, el("tr", {}, el("th", {}, "Drug"), el("th", {}, "PBS status"), el("th", {}, "Items (code · form · benefit type · brands)"))),
      el("tbody", {}, rows))),
  );
}
