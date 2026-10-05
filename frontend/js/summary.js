import { api } from "./api.js";
import { describeEvidence, el, focusEvidence } from "./util.js";

const CITE_ID = String.raw`E\d+(?:\s*[–-]\s*E\d+)?`; // E3, or a range like E1–E24
const CITE_RE = new RegExp(String.raw`\[\s*(${CITE_ID}(?:\s*[,;]\s*${CITE_ID})*)\s*\]`, "g");
const ROLE_LABELS = { primary: "Primary model", comparison: "Comparison model" };
const STATUS_LABELS = { success: "Success", flagged: "Flagged", failed: "Failed" };

const PROVIDER_NAMES = { openai: "OpenAI", anthropic: "Claude" };

/**
 * The AI answer card: the report's Overview first, the rest folded away, with the rule-based alerts, cited
 * evidence and rating underneath. With a comparison report, a switch flips between the two models.
 */
export function renderReports(container, query, { alerts = () => null } = {}) {
  const evidence = new Map((query.evidence?.items || []).map((item) => [item.id, item]));
  // Admin retries append new reports, so show the latest one per role.
  const latest = new Map();
  for (const report of query.reports) latest.set(report.role, report);
  const reports = [...latest.values()];

  const panels = reports.map((report, i) => {
    const panel = reportPanel(report, evidence, alerts);
    panel.hidden = i > 0;
    return panel;
  });
  const tabs = reports.length > 1 && el("div", { class: "segmented", role: "tablist", "aria-label": "Model" },
    reports.map((report, i) => el("button", {
      type: "button", role: "tab", class: i ? "" : "active", "aria-selected": String(!i),
      title: `${ROLE_LABELS[report.role] || report.role}: ${report.model}`,
      onclick: (event) => {
        panels.forEach((panel, j) => { panel.hidden = j !== i; });
        tabs.querySelectorAll("button").forEach((b) => {
          b.classList.toggle("active", b === event.currentTarget);
          b.setAttribute("aria-selected", String(b === event.currentTarget));
        });
      },
    }, PROVIDER_NAMES[report.provider] || report.provider)));

  container.replaceChildren(
    el("div", { class: "ai-head" },
      el("h2", {}, el("span", { class: "ai-spark", "aria-hidden": "true" }, "✦"), " AI summary"),
      tabs || ""),
    ...(panels.length ? panels : [el("p", { class: "muted" }, "No report was generated.")]),
  );
}

function reportPanel(report, evidence, alerts) {
  const meta = [
    report.model,
    report.latency_ms != null && `${(report.latency_ms / 1000).toFixed(1)}s`,
    report.output_tokens != null && `${report.output_tokens} tokens`,
    report.template_version != null && `template v${report.template_version}`,
  ].filter(Boolean).join(" · ");

  const panel = el("div", { class: "report" },
    el("p", { class: "ai-meta small" },
      el("span", { class: `badge ${report.status}` }, STATUS_LABELS[report.status] || report.status),
      el("span", { class: "muted" }, ` ${meta} · written from the evidence below · research use only, not clinical advice`)));

  if (report.status === "failed") {
    panel.append(el("p", { class: "report-error" }, `Generation failed: ${report.error_message || "unknown error"}`),
                 alerts() || "");
    return panel;
  }
  if (report.status === "flagged") {
    panel.append(el("p", { class: "report-warning" }, `⚠ Automatic quality check: ${report.error_message}`));
  }

  const { preview, rest } = splitReport(renderMarkdown(report.content_md || "", report.id));
  const body = el("div", { class: "report-body" }, preview,
    rest.length ? el("details", { class: "full-report" }, el("summary", {}, "Read the full report"), rest) : "");
  body.addEventListener("click", (event) => {
    const link = event.target.closest("a.cite");
    if (!link) return;
    event.preventDefault();
    focusEvidence(link.dataset.eid);
  });
  panel.append(body, alerts() || "", references(report, evidence), feedbackForm(report));
  return panel;
}

/** Show the first section (normally "## Overview") up front and fold the rest of the report. */
function splitReport(html) {
  const holder = el("div", { html });
  const nodes = [...holder.childNodes].filter((n) => n.nodeType === 1 || n.textContent.trim());
  const body = nodes[0]?.tagName === "H2" ? nodes.slice(1) : nodes; // the card title replaces the first heading
  const next = body.findIndex((n) => n.tagName === "H2");
  const preview = next > 0 ? body.slice(0, next) : body.slice(0, 2);
  return { preview: el("div", { class: "report-preview" }, preview), rest: body.slice(preview.length) };
}

function renderMarkdown(markdown, reportId) {
  const linked = markdown.replace(CITE_RE, (_, ids) =>
    ids.split(/\s*[,;]\s*/).map((token) => {
      const label = token.replace(/\s*[–-]\s*/, "–");
      const first = label.split("–")[0];
      return `<a class="cite" href="#ref-${reportId}-${first}" data-eid="${first}">${label}</a>`;
    }).join(""));
  return DOMPurify.sanitize(marked.parse(linked));
}

function references(report, evidence) {
  const list = el("ol");
  for (const eid of report.citations) {
    const item = evidence.get(eid);
    if (!item) continue;
    list.append(el("li", { id: `ref-${report.id}-${eid}` },
      el("span", { class: "eid" }, `[${eid}]`),
      item.url ? el("a", { href: item.url, target: "_blank", rel: "noopener" }, describeEvidence(item))
               : describeEvidence(item)));
  }
  return el("details", { class: "references" },
    el("summary", {}, `Cited evidence (${report.citations.length})`), list);
}

function feedbackForm(report) {
  const name = `rating-${report.id}`;
  const stars = el("span", { class: "stars", role: "radiogroup", "aria-label": "Rating" });
  for (let value = 5; value >= 1; value--) {
    const id = `${name}-${value}`;
    stars.append(
      el("input", { type: "radio", name, id, value }),
      el("label", { for: id, title: `${value} star${value > 1 ? "s" : ""}` }, "★"));
  }
  const comment = el("textarea", { rows: 2, placeholder: "Comments (what was good, what was inaccurate)" });
  const suggestion = el("textarea", { rows: 2, placeholder: "Suggestions for improvement" });
  const summary = el("span", { class: "muted small" },
    report.feedback_count ? `${report.feedback_count} ratings so far, average ${report.avg_rating ?? "–"}` : "");
  const submit = el("button", { type: "submit", class: "small" }, "Submit feedback");

  const form = el("form", { class: "stack" },
    el("div", { class: "row" }, el("strong", { class: "small" }, "Answer quality"), stars, summary),
    comment, suggestion, el("div", {}, submit));

  form.addEventListener("submit", async (event) => {
    event.preventDefault();
    const checked = form.querySelector(`input[name="${name}"]:checked`);
    const body = {
      rating: checked ? Number(checked.value) : null,
      comment: comment.value,
      suggestion: suggestion.value,
    };
    submit.disabled = true;
    try {
      const { report: updated } = await api.feedback(report.id, body);
      summary.textContent = `Thanks for your feedback! ${updated.feedback_count} ratings, average ${updated.avg_rating ?? "–"}`;
      comment.value = suggestion.value = "";
    } catch (err) {
      summary.textContent = `Could not submit: ${err.message}`;
    } finally {
      submit.disabled = false;
    }
  });
  const ratings = report.feedback_count ? ` · ${report.feedback_count} so far, average ${report.avg_rating ?? "–"}` : "";
  return el("details", { class: "feedback" }, el("summary", {}, `Rate this answer${ratings}`), form);
}
