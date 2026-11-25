// Set window.DGI_API_BASE (e.g. "http://localhost:5000") if the frontend is served separately from Flask.
const API_BASE = window.DGI_API_BASE || "";

// CSRF protection (backend/app/csrf.py): every state-changing request carries the session's token in X-CSRF-Token.
// Every /api/ response sends the current token back (it changes when the user logs in or out), so it is kept here.
const UNSAFE = new Set(["POST", "PUT", "PATCH", "DELETE"]);
let csrfToken = null;

function keepCsrfToken(resp) {
  const token = resp.headers.get("X-CSRF-Token");
  if (token) csrfToken = token;
}

async function ensureCsrfToken(fresh = false) {
  if (csrfToken && !fresh) return csrfToken;
  const resp = await fetch(`${API_BASE}/api/auth/csrf`, { credentials: "same-origin" });
  keepCsrfToken(resp);
  return csrfToken;
}

/** fetch() with the CSRF token added; a 403 "csrf" answer (e.g. the session expired) is retried once with a new token. */
async function send(method, path, options) {
  const unsafe = UNSAFE.has(method);
  for (let attempt = 0; ; attempt++) {
    const headers = { ...options.headers };
    if (unsafe) headers["X-CSRF-Token"] = await ensureCsrfToken(attempt > 0);
    const resp = await fetch(API_BASE + path, { ...options, method, headers, credentials: "same-origin" });
    keepCsrfToken(resp);
    if (unsafe && attempt === 0 && resp.status === 403) {
      const data = await resp.clone().json().catch(() => ({}));
      if (data.code === "csrf") continue;
    }
    return resp;
  }
}

async function request(method, path, { body, form, headers = {} } = {}) {
  const options = { headers: { ...headers } };
  if (form) {
    options.body = form;
  } else if (body !== undefined) {
    options.headers["Content-Type"] = "application/json";
    options.body = JSON.stringify(body);
  }
  const resp = await send(method, path, options);
  const data = await resp.json().catch(() => ({}));
  if (!resp.ok) {
    // 401: the action needs an account (patient data) or the session has ended; auth.js opens the login panel.
    if (resp.status === 401 && !path.startsWith("/api/auth/")) {
      document.dispatchEvent(new CustomEvent("auth:required", { detail: data.error }));
    }
    // The body travels with the error, e.g. the suggestions when an entity needs confirming (HTTP 422).
    throw Object.assign(new Error(data.error || `HTTP ${resp.status}`), { status: resp.status, data });
  }
  return data;
}

/** A file download (the Word report): the blob and the file name the server gave it. */
async function download(method, path, body) {
  const resp = await send(method, path, {
    headers: { "Content-Type": "application/json" }, body: body === undefined ? undefined : JSON.stringify(body),
  });
  if (!resp.ok) {
    const data = await resp.json().catch(() => ({}));
    throw new Error(data.error || `HTTP ${resp.status}`);
  }
  const name = /filename\*?=(?:UTF-8'')?"?([^";]+)"?/i.exec(resp.headers.get("Content-Disposition") || "")?.[1];
  return { blob: await resp.blob(), filename: name ? decodeURIComponent(name) : "Pharmagenix_report.docx" };
}

export const api = {
  meta: () => request("GET", "/api/meta"),
  vcfOptions: () => request("GET", "/api/vcf/options"),
  vcfExplore: (form) => request("POST", "/api/vcf/explore", { form }),
  vcfExploreExample: (body) => request("POST", "/api/vcf/explore", { body }),
  vcfIsm: (body) => request("POST", "/api/vcf/ism", { body }),
  explore: (body) => request("POST", "/api/explore", { body }),
  getQuery: (id) => request("GET", `/api/queries/${id}`),
  history: () => request("GET", "/api/queries?mine=1&limit=10"),
  ask: (id, body) => request("POST", `/api/queries/${id}/ask`, { body }),
  reportDocx: (id, body) => download("POST", `/api/queries/${id}/report.docx`, body),
  feedback: (reportId, body) => request("POST", `/api/reports/${reportId}/feedback`, { body }),
  me: () => request("GET", "/api/auth/me"),
  login: (body) => request("POST", "/api/auth/login", { body }),
  register: (body) => request("POST", "/api/auth/register", { body }),
  logout: () => request("POST", "/api/auth/logout"),
};

export function adminApi(token) {
  const headers = { "X-Admin-Token": token };
  const get = (path, params = {}) => {
    const qs = new URLSearchParams(Object.entries(params).filter(([, v]) => v !== "" && v != null));
    return request("GET", `/api/admin${path}${qs.size ? `?${qs}` : ""}`, { headers });
  };
  const post = (path, body = {}) => request("POST", `/api/admin${path}`, { body, headers });
  return {
    logs: (params) => get("/prompt-logs", params),
    log: (id) => get(`/prompt-logs/${id}`),
    templates: () => get("/templates"),
    createTemplate: (body) => post("/templates", body),
    activateTemplate: (id) => post(`/templates/${id}/activate`),
    previewTemplate: (body) => post("/templates/preview", body),
    failures: (params) => get("/failures", params),
    retry: (reportId) => post(`/reports/${reportId}/retry`),
    feedback: (params) => get("/feedback", params),
    stats: () => get("/stats"),
    evaluation: () => get("/evaluation"),
  };
}
