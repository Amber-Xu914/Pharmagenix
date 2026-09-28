// Set window.DGI_API_BASE (e.g. "http://localhost:5000") if the frontend is served separately from Flask.
const API_BASE = window.DGI_API_BASE || "";

async function request(method, path, { body, form, headers = {} } = {}) {
  const options = { method, headers: { ...headers } };
  if (form) {
    options.body = form;
  } else if (body !== undefined) {
    options.headers["Content-Type"] = "application/json";
    options.body = JSON.stringify(body);
  }
  const resp = await fetch(API_BASE + path, options);
  const data = await resp.json().catch(() => ({}));
  if (!resp.ok) throw new Error(data.error || `HTTP ${resp.status}`);
  return data;
}

export const api = {
  meta: () => request("GET", "/api/meta"),
  parsePanel(file) {
    const form = new FormData();
    form.append("file", file);
    return request("POST", "/api/panels/parse", { form });
  },
  explore: (body) => request("POST", "/api/explore", { body }),
  getQuery: (id) => request("GET", `/api/queries/${id}`),
  ask: (id, body) => request("POST", `/api/queries/${id}/ask`, { body }),
  feedback: (reportId, body) => request("POST", `/api/reports/${reportId}/feedback`, { body }),
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
  };
}
