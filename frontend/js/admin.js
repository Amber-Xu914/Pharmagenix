import { adminApi } from "./api.js";
import { el, formatDate, labelTableCells, latestOnly } from "./util.js";

const $ = (id) => document.getElementById(id);
const TOKEN_KEY = "dgi-admin-token";
let admin = null;
let templates = [];
labelTableCells();
// One per view: a slow response to an earlier click or filter must not overwrite a newer one
const logsRequest = latestOnly();
const logRequest = latestOnly();
const templatesRequest = latestOnly();
const failuresRequest = latestOnly();
const feedbackRequest = latestOnly();
const statsRequest = latestOnly();
const evaluationRequest = latestOnly();

// ---------- Login ----------

function storedToken() {
  try { return sessionStorage.getItem(TOKEN_KEY) || ""; } catch { return ""; }
}

async function login(token) {
  const candidate = adminApi(token);
  await candidate.stats(); // throws on a wrong token
  admin = candidate;
  if (token) try { sessionStorage.setItem(TOKEN_KEY, token); } catch { /* private mode */ }
  $("login-card").classList.add("hidden");
  $("admin-app").classList.remove("hidden");
  showTab("logs");
}

$("login-form").addEventListener("submit", async (event) => {
  event.preventDefault();
  $("login-error").textContent = "";
  try {
    await login($("admin-token").value.trim());
  } catch (err) {
    $("login-error").textContent = err.message;
  }
});

// ---------- Tabs ----------

const loaders = { logs: loadLogs, templates: loadTemplates, failures: loadFailures, feedback: loadFeedback, stats: loadStats,
  evaluation: loadEvaluation };

function showTab(name) {
  document.querySelectorAll("#tabs button").forEach((b) => b.classList.toggle("active", b.dataset.tab === name));
  document.querySelectorAll("[data-panel]").forEach((p) => p.classList.toggle("hidden", p.dataset.panel !== name));
  loaders[name]().catch((err) => alert(err.message));
}

$("tabs").addEventListener("click", (event) => {
  const tab = event.target.closest("button")?.dataset.tab;
  if (tab) showTab(tab);
});

function renderTable(table, headers, rows) {
  table.replaceChildren(
    el("thead", {}, el("tr", {}, headers.map((h) => el("th", {}, h)))),
    el("tbody", {}, rows.length ? rows : el("tr", {}, el("td", { colspan: headers.length, class: "muted" }, "No data yet"))));
}

function pager(target, data, reload) {
  const pages = Math.max(1, Math.ceil(data.total / data.per_page));
  target.replaceChildren(
    `${data.total} total · page ${data.page}/${pages} `,
    el("button", { class: "secondary small", disabled: data.page <= 1, onclick: () => reload(data.page - 1) }, "Previous"), " ",
    el("button", { class: "secondary small", disabled: data.page >= pages, onclick: () => reload(data.page + 1) }, "Next"));
}

const statusBadge = (status) => el("span", { class: `badge ${status}` }, status);

// ---------- Prompt logs ----------

async function loadLogs(page = 1) {
  const isLatest = logsRequest();
  const data = await admin.logs({ page, status: $("log-status").value, provider: $("log-provider").value });
  if (!isLatest()) return;
  renderTable($("log-table"), ["ID", "Time", "Model", "Status", "Latency", "Tokens"], data.items.map((log) =>
    el("tr", { class: "clickable", onclick: () => showLog(log.id) },
      el("td", {}, log.id),
      el("td", {}, formatDate(log.created_at)),
      el("td", {}, log.model),
      el("td", {}, statusBadge(log.status)),
      el("td", {}, log.latency_ms != null ? `${log.latency_ms} ms` : ""),
      el("td", {}, log.input_tokens != null ? `${log.input_tokens} / ${log.output_tokens}` : ""))));
  pager($("log-pager"), data, loadLogs);
}

async function showLog(id) {
  const isLatest = logRequest();
  $("log-detail").replaceChildren(el("p", { class: "muted" }, `Loading log #${id}…`));
  const log = await admin.log(id);
  if (!isLatest()) return;
  $("log-detail").replaceChildren(
    el("h3", {}, `Log #${log.id} · ${log.model} `, statusBadge(log.status)),
    el("p", { class: "small" },
      `Query `, el("a", { href: `/?q=${log.query_id}`, target: "_blank" }, `#${log.query_id}`),
      ` · report #${log.report_id} · template #${log.template_id ?? "–"} · ${formatDate(log.created_at)}`),
    log.error_message ? el("p", { class: "report-error small" }, log.error_message) : "",
    el("h4", {}, "System prompt"), el("pre", { class: "block" }, log.system_prompt || ""),
    el("h4", {}, "User prompt"), el("pre", { class: "block" }, log.user_prompt || ""),
    el("h4", {}, "Raw response"), el("pre", { class: "block" }, log.raw_response || "(none)"));
}

$("log-status").addEventListener("change", () => loadLogs());
$("log-provider").addEventListener("change", () => loadLogs());
$("log-refresh").addEventListener("click", () => loadLogs());

// ---------- Templates ----------

async function loadTemplates() {
  const isLatest = templatesRequest();
  const latest = await admin.templates();
  if (!isLatest()) return;
  templates = latest;
  renderTable($("template-table"), ["Name", "Version", "Status", "Created", "Notes", ""], templates.map((t) =>
    el("tr", { class: "clickable", onclick: () => editTemplate(t) },
      el("td", {}, t.name),
      el("td", {}, `v${t.version}`),
      el("td", {}, t.is_active ? el("span", { class: "badge success" }, "Active") : ""),
      el("td", {}, formatDate(t.created_at)),
      el("td", {}, t.notes || ""),
      el("td", {}, t.is_active ? "" : el("button", {
        class: "secondary small",
        onclick: async (e) => { e.stopPropagation(); await admin.activateTemplate(t.id); loadTemplates(); },
      }, "Activate")))));
  const active = templates.find((t) => t.is_active);
  if (active && !$("tpl-system").value) editTemplate(active);
}

function editTemplate(t) {
  $("tpl-name").value = t.name;
  $("tpl-system").value = t.system_prompt;
  $("tpl-user").value = t.user_template;
  $("tpl-notes").value = "";
  $("tpl-source").textContent = `Editing from v${t.version}`;
}

function templateBody() {
  return {
    name: $("tpl-name").value.trim(),
    system_prompt: $("tpl-system").value,
    user_template: $("tpl-user").value,
    notes: $("tpl-notes").value.trim(),
  };
}

$("template-form").addEventListener("submit", async (event) => {
  event.preventDefault();
  // Each save creates a new version: the button stays disabled until the answer arrives, so a double click
  // can't save the same template twice.
  const button = event.submitter || $("template-form").querySelector("button[type=submit]");
  if (button.disabled) return;
  button.disabled = true;
  try {
    const created = await admin.createTemplate({ ...templateBody(), activate: $("tpl-activate").checked });
    $("tpl-message").textContent = `Saved ${created.name} v${created.version}${created.is_active ? " (now active)" : ""}`;
    loadTemplates();
  } catch (err) {
    $("tpl-message").textContent = `Could not save: ${err.message}`;
  } finally {
    button.disabled = false;
  }
});

$("tpl-preview").addEventListener("click", async () => {
  const out = $("tpl-preview-out");
  try {
    const result = await admin.previewTemplate({ ...templateBody(), query_id: Number($("tpl-preview-query").value) });
    out.textContent = `--- SYSTEM ---\n${result.system_prompt}\n\n--- USER ---\n${result.user_prompt}`;
  } catch (err) {
    out.textContent = `Preview failed: ${err.message}`;
  }
  out.classList.remove("hidden");
});

// ---------- Failures ----------

async function loadFailures(page = 1) {
  const isLatest = failuresRequest();
  const data = await admin.failures({ page });
  if (!isLatest()) return;
  renderTable($("failure-table"), ["Report", "Time", "Query", "Model", "Status", "Reason", ""], data.items.map((r) =>
    el("tr", {},
      el("td", {}, `#${r.id}`),
      el("td", {}, formatDate(r.created_at)),
      el("td", {}, el("a", { href: `/?q=${r.query_id}`, target: "_blank" }, `#${r.query_id} ${r.query.entities.join(", ")}`)),
      el("td", {}, r.model),
      el("td", {}, statusBadge(r.status)),
      el("td", { class: "small" }, r.error_message || ""),
      el("td", {},
        el("button", { class: "small", onclick: (e) => retry(r.id, e.target) }, "Retry"), " ",
        r.prompt_log_ids.length ? el("button", {
          class: "secondary small",
          onclick: () => { showTab("logs"); showLog(r.prompt_log_ids[0]); },
        }, "View log") : ""))));
  pager($("failure-pager"), data, loadFailures);
}

async function retry(reportId, button) {
  button.disabled = true;
  button.textContent = "Generating…";
  try {
    const report = await admin.retry(reportId);
    button.textContent = `New report #${report.id}: ${report.status}`;
  } catch (err) {
    button.textContent = "Retry failed";
    alert(err.message);
  }
}

$("failure-refresh").addEventListener("click", () => loadFailures());

// ---------- Feedback ----------

async function loadFeedback(page = 1) {
  const isLatest = feedbackRequest();
  const data = await admin.feedback({ page, max_rating: $("feedback-max").value });
  if (!isLatest()) return;
  renderTable($("feedback-table"), ["Time", "Query", "Model", "Rating", "Comments", "Suggestions"], data.items.map((f) =>
    el("tr", {},
      el("td", {}, formatDate(f.created_at)),
      el("td", {}, el("a", { href: `/?q=${f.query_id}`, target: "_blank" }, `#${f.query_id} ${f.entities.join(", ")}`)),
      el("td", {}, f.model),
      el("td", {}, f.rating ? "★".repeat(f.rating) : "–"),
      el("td", {}, f.comment || ""),
      el("td", {}, f.suggestion || ""))));
  pager($("feedback-pager"), data, loadFeedback);
}

$("feedback-max").addEventListener("change", () => loadFeedback());

// ---------- Stats ----------

async function loadStats() {
  const isLatest = statsRequest();
  const s = await admin.stats();
  if (!isLatest()) return;
  const stat = (label, value, sub = "") =>
    el("div", { class: "stat" }, el("div", { class: "muted small" }, label), el("div", { class: "num" }, value), sub && el("div", { class: "small muted" }, sub));
  $("stats").replaceChildren(
    stat("Total queries", s.queries),
    stat("Total feedback", s.feedback),
    ...s.models.map((m) => stat(m.model, m.avg_rating ?? "–",
      `Average rating · ${m.reports} reports · ${m.failed} failed · ${m.flagged} flagged · ${m.avg_latency_ms ?? "–"} ms average`)),
    ...["hgnc", "chembl", "cpic", "clinpgx", "dgidb", "fda", "oncotree", "intogen", "civic", "hotspots", "cbioportal", "pbs"].map((source) => {
      const r = s.reference_data[source];
      return stat(`Reference data: ${source.toUpperCase()}`, r ? "Loaded" : "Not imported",
        r ? `${r.version} · imported ${formatDate(r.imported_at)}` : "Run: flask --app run import-data");
    }));
}

// ---------- Evaluation ----------
// Workflow 4 on the 8 mock VCFs against their expected answers (backend/app/evaluation.py, `flask --app run
// eval-metrics`): drug recommendation as a binary classifier and variant tiering as a multi-class one.

const pct = (x) => (x == null ? "–" : `${Math.round(x * 1000) / 10}%`);

function confusionTable(labels, matrix, caption) {
  return el("div", { class: "table-wrap" }, el("table", { class: "confusion" },
    el("caption", {}, caption),
    el("thead", {}, el("tr", {}, el("th", {}, "Expected ↓ / Predicted →"), labels.map((l) => el("th", {}, l)))),
    el("tbody", {}, matrix.map((row, i) => el("tr", {}, el("th", {}, labels[i]),
      row.map((n, j) => el("td", { class: !n ? "" : i === j ? "hit" : "miss" }, String(n))))))));
}

async function loadEvaluation() {
  const isLatest = evaluationRequest();
  const box = $("evaluation");
  let e;
  try {
    e = await admin.evaluation();
  } catch (err) {
    box.replaceChildren(el("p", { class: "muted" }, err.message));
    return;
  }
  if (!isLatest()) return;
  const d = e.drug_recommendation;
  const t = e.variant_tiering;
  const c = d.confusion;
  const stat = (label, value, sub) =>
    el("div", { class: "stat" }, el("div", { class: "muted small" }, label), el("div", { class: "num" }, value),
      el("div", { class: "small muted" }, sub));
  box.replaceChildren(
    el("p", { class: "small muted" }, `Workflow 4 (QC, tiering, drug tracks; no LLM) on ${e.cases.length} synthetic `
      + `mock VCFs, scored against expected answers from CIViC, FDA labels and NCCN. Run ${formatDate(e.generated_at)} `
      + "with flask --app run eval-metrics."),
    el("h3", {}, "Drug recommendation (binary)"),
    el("p", { class: "small muted" }, "Positive = the drug is recommended (established track, not down-ranked). "
      + "Each case's guideline drugs are positives; drugs with resistance or from QC artefacts are negatives."),
    el("div", { class: "stat-grid" },
      stat("Precision", pct(d.precision), `TP ${c.tp} / (TP + FP ${c.fp})`),
      stat("Recall (sensitivity)", pct(d.recall), `TP ${c.tp} / (TP + FN ${c.fn})`),
      stat("F1-score", pct(d.f1), "harmonic mean of precision and recall"),
      stat("Specificity", pct(d.specificity), `TN ${c.tn} / (TN + FP ${c.fp})`),
      stat("Accuracy", pct(d.accuracy), `${c.tp + c.tn} of ${d.n} labelled drugs`)),
    confusionTable(["Recommended", "Not recommended"], [[c.tp, c.fn], [c.fp, c.tn]],
      `Confusion matrix: drug recommendation (n = ${d.n})`),
    el("h3", {}, "Variant tiering (multi-class)"),
    el("p", { class: "small muted" }, `Accuracy ${pct(t.accuracy)} · macro F1 ${pct(t.macro_f1)} over ${t.n} labelled `
      + "variants. Where a case accepts two tiers (e.g. STK11 Q37* Tier 1 or 2), an accepted tier counts as correct."),
    confusionTable(t.labels, t.matrix, `Confusion matrix: variant tier (n = ${t.n})`),
    el("div", { class: "table-wrap" }, el("table", {},
      el("thead", {}, el("tr", {}, ["Class", "Support", "Precision", "Recall", "F1"].map((h) => el("th", {}, h)))),
      el("tbody", {}, Object.entries(t.per_class).map(([label, m]) => el("tr", {},
        el("td", {}, label), el("td", {}, m.support), el("td", {}, pct(m.precision)), el("td", {}, pct(m.recall)),
        el("td", {}, pct(m.f1))))))),
    el("h3", {}, "Per case"),
    el("div", { class: "table-wrap" }, el("table", {},
      el("thead", {}, el("tr", {}, ["Mock VCF", "Cancer", "Drugs TP / FP / FN / TN", "Tiers correct",
        "Other recommendations (not scored)"].map((h) => el("th", {}, h)))),
      el("tbody", {}, e.cases.map((x) => el("tr", {},
        el("td", {}, x.example), el("td", {}, x.cancer_type),
        el("td", {}, `${x.drug_metrics.tp} / ${x.drug_metrics.fp} / ${x.drug_metrics.fn} / ${x.drug_metrics.tn}`),
        el("td", {}, `${x.tiers_correct} / ${x.tiers.length}`),
        el("td", { class: "small" }, x.unscored_recommendations.join(", ") || "–")))))),
    el("h3", {}, "Reliability and limitations"),
    el("ul", { class: "small" },
      el("li", {}, "Small test set: 8 synthetic cases, 25 labelled drugs and 25 labelled variants. A perfect score "
        + "shows the rules behave as designed on these cases; it does not estimate accuracy on real patients."),
      el("li", {}, "The expected answers were written by the team from CIViC, FDA labels and NCCN, not by an "
        + "independent clinical panel, and the same cases were used while building the rules (no held-out set)."),
      el("li", {}, "Only labelled drugs are scored. Other recommendations (listed above) may be correct but are not "
        + "counted, so precision covers the guideline drugs, not every drug the system shows."),
      el("li", {}, "Fusions, copy-number changes and PD-L1 are outside the pipeline, so cases that depend on them "
        + "(e.g. ALK, ROS1, MET amplification) are not represented.")),
  );
}

// ---------- Boot ----------

// An admin account already logged in on the explorer needs no token; otherwise try the token from this tab.
login("").catch(() => {
  const saved = storedToken();
  if (!saved) return;
  $("admin-token").value = saved;
  login(saved).catch(() => { /* token expired or changed: stay on the login form */ });
});
