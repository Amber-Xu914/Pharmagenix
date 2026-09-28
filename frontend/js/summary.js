import { api } from "./api.js";
import { describeEvidence, el, focusEvidence } from "./util.js";

const CITE_RE = /\[\s*(E\d+(?:\s*[,;]\s*E\d+)*)\s*\]/g;
const ROLE_LABELS = { primary: "Primary model", comparison: "Comparison model" };
const STATUS_LABELS = { success: "Success", flagged: "Flagged", failed: "Failed" };

export function renderReports(container, query) {
  container.replaceChildren();
  const evidence = new Map((query.evidence?.items || []).map((item) => [item.id, item]));
  // Admin retries append new reports, so show the latest one per role.
  const latest = new Map();
  for (const report of query.reports) latest.set(report.role, report);
  if (!latest.size) container.append(el("p", { class: "muted" }, "No report was generated."));
  for (const report of latest.values()) container.append(reportCard(report, evidence));
}

function reportCard(report, evidence) {
  const meta = [
    report.latency_ms != null && `${(report.latency_ms / 1000).toFixed(1)}s`,
    report.output_tokens != null && `${report.output_tokens} tokens`,
    report.template_version != null && `template v${report.template_version}`,
  ].filter(Boolean).join(" · ");

  const card = el("article", { class: "report" },
    el("div", { class: "report-head" },
      el("span", { class: "model" }, report.model),
      el("span", { class: "badge" }, ROLE_LABELS[report.role] || report.role),
      el("span", { class: `badge ${report.status}` }, STATUS_LABELS[report.status] || report.status),
      el("span", { class: "muted small" }, meta),
    ),
  );

  if (report.status === "failed") {
    card.append(el("p", { class: "report-error" }, `Generation failed: ${report.error_message || "unknown error"}`));
    return card;
  }
  if (report.status === "flagged") {
    card.append(el("p", { class: "report-warning" }, `⚠ Automatic quality check: ${report.error_message}`));
  }

  const body = el("div", { class: "report-body", html: renderMarkdown(report.content_md || "", report.id) });
  body.addEventListener("click", (event) => {
    const link = event.target.closest("a.cite");
    if (link) focusEvidence(link.dataset.eid);
  });
  card.append(body, references(report, evidence), feedbackForm(report));
  return card;
}

function renderMarkdown(markdown, reportId) {
  const linked = markdown.replace(CITE_RE, (_, ids) =>
    ids.split(/\s*[,;]\s*/)
      .map((id) => `<a class="cite" href="#ref-${reportId}-${id}" data-eid="${id}">${id}</a>`)
      .join(""));
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
  return el("details", { class: "references", open: true },
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

  const form = el("form", { class: "feedback stack" },
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
  return form;
}
