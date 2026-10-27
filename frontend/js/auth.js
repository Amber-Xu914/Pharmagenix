import { api } from "./api.js";
import { el, formatDate } from "./util.js";

const $ = (id) => document.getElementById(id);
const state = { user: null, mode: "login" };

const MODES = {
  login: { submit: "Log in", autocomplete: "current-password", call: api.login },
  register: { submit: "Create account", autocomplete: "new-password", call: api.register },
};

/** The panel shown by the top-right button: the login form when signed out, the user menu when signed in. */
function activePanel() {
  return state.user ? $("user-menu") : $("auth-panel");
}

function setOpen(open) {
  $("auth-panel").classList.toggle("hidden", !open || !!state.user);
  $("user-menu").classList.toggle("hidden", !open || !state.user);
  $("account-btn").setAttribute("aria-expanded", String(open));
  if (open && !state.user) $("auth-email").focus();
  if (open && state.user) loadHistory();
}

/** The user's last searches (queries.user_id), newest first; each reopens its stored result. */
async function loadHistory() {
  const list = $("history-list");
  list.replaceChildren(el("li", { class: "muted" }, "Loading…"));
  try {
    const queries = await api.history();
    list.replaceChildren(...(queries.length ? queries.map((q) => el("li", {},
      el("a", { href: `/?q=${q.id}` }, q.entity_type === "vcf" ? `🧪 ${q.entities[0]}` : `🧬 ${q.entities[0]}`),
      el("span", { class: "muted" }, ` · ${[q.cancer_type, q.followup_question && "follow-up",
        q.status !== "complete" && q.status, formatDate(q.created_at)].filter(Boolean).join(" · ")}`)))
      : [el("li", { class: "muted" }, "No searches yet: they are saved here while you are logged in.")]));
  } catch (err) {
    list.replaceChildren(el("li", { class: "report-error" }, err.message));
  }
}

function isOpen() {
  return !activePanel().classList.contains("hidden");
}

function setUser(user) {
  state.user = user;
  $("account-btn").textContent = user ? `🧑‍🔬 ${user.email}` : "Log in";
  $("account-btn").title = user?.email || "";
  $("user-email").textContent = user?.email || "";
  $("admin-link").classList.toggle("hidden", !user?.is_admin);
  document.dispatchEvent(new CustomEvent("auth:change", { detail: user }));
}

function setMode(mode) {
  state.mode = mode;
  document.querySelectorAll("#auth-panel [data-mode]").forEach((tab) => {
    const active = tab.dataset.mode === mode;
    tab.classList.toggle("active", active);
    tab.setAttribute("aria-selected", String(active));
  });
  $("auth-submit").textContent = MODES[mode].submit;
  $("auth-password").autocomplete = MODES[mode].autocomplete;
  $("auth-password").minLength = mode === "register" ? 8 : 0;
  $("auth-hint").classList.toggle("hidden", mode !== "register");
  showError("");
}

function showError(message) {
  $("auth-error").textContent = message;
  $("auth-error").classList.toggle("hidden", !message);
}

$("account-btn").addEventListener("click", () => setOpen(!isOpen()));

document.querySelectorAll("#auth-panel [data-mode]").forEach((tab) =>
  tab.addEventListener("click", () => setMode(tab.dataset.mode)));

$("auth-form").addEventListener("submit", async (event) => {
  event.preventDefault();
  const body = { email: $("auth-email").value.trim(), password: $("auth-password").value };
  $("auth-submit").disabled = true;
  showError("");
  try {
    const { user } = await MODES[state.mode].call(body);
    event.target.reset();
    setMode("login"); // after logging out, the panel should offer "Log in", not "Create account"
    setUser(user);
    setOpen(false);
  } catch (err) {
    showError(err.message);
  } finally {
    $("auth-submit").disabled = false;
  }
});

$("logout-btn").addEventListener("click", async () => {
  try {
    await api.logout();
  } finally {
    setUser(null);
    setOpen(false);
  }
});

// Close the panel on Escape or a click outside it.
document.addEventListener("keydown", (event) => {
  if (event.key === "Escape" && isOpen()) {
    setOpen(false);
    $("account-btn").focus();
  }
});
document.addEventListener("click", (event) => {
  if (isOpen() && !event.target.closest(".account")) setOpen(false);
});

setMode("login");
api.me().then(({ user }) => setUser(user)).catch(() => setUser(null));
