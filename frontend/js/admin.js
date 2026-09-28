import { adminApi } from "./api.js";
import { el, formatDate } from "./util.js";

const $ = (id) => document.getElementById(id);
const TOKEN_KEY = "dgi-admin-token";
let admin = null;
let templates = [];

// ---------- Login ----------

function storedToken() {
  try { return sessionStorage.getItem(TOKEN_KEY) || ""; } catch { return ""; }
}

async function login(token) {
  const candidate = adminApi(token);
  await candidate.stats(); // throws on a wrong token
  admin = candidate;
  try { sessionStorage.setItem(TOKEN_KEY, token); } catch { /* private mode */ }
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

const loaders = { logs: loadLogs, templates: loadTemplates, failures: loadFailures, feedback: loadFeedback, stats: loadStats };

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
  const data = await admin.logs({ page, status: $("log-status").value, provider: $("log-provider").value });
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
  const log = await admin.log(id);
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
  templates = await admin.templates();
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
  try {
    const created = await admin.createTemplate({ ...templateBody(), activate: $("tpl-activate").checked });
    $("tpl-message").textContent = `Saved ${created.name} v${created.version}${created.is_active ? " (now active)" : ""}`;
    loadTemplates();
  } catch (err) {
    $("tpl-message").textContent = `Could not save: ${err.message}`;
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
  const data = await admin.failures({ page });
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
  const data = await admin.feedback({ page, max_rating: $("feedback-max").value });
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
  const s = await admin.stats();
  const stat = (label, value, sub = "") =>
    el("div", { class: "stat" }, el("div", { class: "muted small" }, label), el("div", { class: "num" }, value), sub && el("div", { class: "small muted" }, sub));
  $("stats").replaceChildren(
    stat("Total queries", s.queries),
    stat("Total feedback", s.feedback),
    ...s.models.map((m) => stat(m.model, m.avg_rating ?? "–",
      `Average rating · ${m.reports} reports · ${m.failed} failed · ${m.flagged} flagged · ${m.avg_latency_ms ?? "–"} ms average`)),
    ...["hgnc", "chembl", "cpic", "pbs"].map((source) => {
      const r = s.reference_data[source];
      return stat(`Reference data: ${source.toUpperCase()}`, r ? "Loaded" : "Not imported",
        r ? `${r.version} · imported ${formatDate(r.imported_at)}` : "Run: flask --app run import-data");
    }));
}

// ---------- Boot ----------

const saved = storedToken();
if (saved) {
  $("admin-token").value = saved;
  login(saved).catch(() => { /* token expired or changed: stay on the login form */ });
}
