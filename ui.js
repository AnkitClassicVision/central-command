const root = document.querySelector("#board");
const storageKey = "cc-view";
const filterStorageKey = "cc-filters";
const reducedMotion = matchMedia("(prefers-reduced-motion: reduce)");
let filters = { location: "", agent: "", surface: "", instance: "", state: "", query: "" };
let reading = false;
function readStorage(key) { try { return localStorage.getItem(key); } catch { return null; } }
function writeStorage(key, value) { try { localStorage.setItem(key, value); } catch {} }
try {
  const saved = JSON.parse(readStorage(filterStorageKey) || "{}");
  for (const key of Object.keys(filters)) if (typeof saved?.[key] === "string") filters[key] = saved[key];
} catch {}
function animateLayout(change) {
  const selector = document.body.dataset.view === "board" ? ".kanban .session" : ".list .session";
  const before = new Map();
  if (!reducedMotion.matches) root.querySelectorAll(selector).forEach(row => {
    if (!row.hidden) before.set(row, row.getBoundingClientRect());
  });
  change();
  if (reducedMotion.matches) return;
  root.querySelectorAll(selector).forEach(row => {
    if (row.hidden) return;
    const prior = before.get(row);
    const next = row.getBoundingClientRect();
    const dx = prior ? prior.left - next.left : 0;
    const dy = prior ? prior.top - next.top : 5;
    if (dx || dy || !prior) row.animate([
      { transform: `translate(${dx}px, ${dy}px)`, opacity: prior ? 1 : 0 },
      { transform: "none", opacity: 1 }
    ], { duration: 240, easing: "cubic-bezier(.2,.8,.2,1)" });
  });
}
function applyFilters() {
  const rows = [...root.querySelectorAll(".list .session")];
  const matchesBase = row => (!filters.location || row.dataset.location === filters.location) &&
    (!filters.agent || JSON.parse(row.dataset.agents || "[]").includes(filters.agent)) &&
    (!filters.surface || row.dataset.surface === filters.surface) &&
    (!filters.instance || row.dataset.instance === filters.instance) &&
    (!filters.query || (row.dataset.search || "").includes(filters.query.toLowerCase().trim()));
  root.querySelectorAll(".session").forEach(row => {
    row.hidden = !matchesBase(row) || Boolean(filters.state && row.dataset.state !== filters.state);
  });
  const visible = rows.filter(row => !row.hidden).length;
  const status = root.querySelector(".filter-status");
  const message = `Showing ${visible} of ${rows.length} sessions`;
  if (status.textContent !== message) status.textContent = message;
  root.querySelector(".filter-empty").hidden = !(rows.length && !visible);
  root.querySelector(".all-empty").hidden = Boolean(rows.length);
  root.querySelector(".clear-filters").disabled = !Object.values(filters).some(Boolean);
  root.querySelectorAll(".column").forEach(column => {
    const count = [...column.querySelectorAll(".session")].filter(row => !row.hidden).length;
    column.querySelector(".count").textContent = count;
    column.querySelector(".column-empty").hidden = Boolean(count);
  });
  root.querySelectorAll("[data-state]").forEach(button => {
    if (button.tagName !== "BUTTON") return;
    button.setAttribute("aria-pressed", button.dataset.state === filters.state ? "true" : "false");
    button.querySelector("span").textContent = rows.filter(row => matchesBase(row) && (!button.dataset.state || row.dataset.state === button.dataset.state)).length;
  });
  root.querySelectorAll("[data-location-choice]").forEach(button => {
    button.setAttribute("aria-pressed", button.dataset.locationChoice === filters.location ? "true" : "false");
    button.querySelector(".machine-count").textContent = rows.filter(row => row.dataset.location === button.dataset.locationChoice).length;
  });
  document.querySelector(".nav-total").textContent = rows.length;
}
function restoreFilters() {
  root.querySelectorAll("[data-filter]").forEach(select => {
    const value = filters[select.dataset.filter];
    if (value && ![...select.options].some(option => option.value === value)) select.add(new Option(`${select.dataset.filter === "instance" ? "Selected instance" : value} (no sessions)`, value));
    select.value = value;
  });
  const search = root.querySelector("#session-search");
  if (search.value !== filters.query) search.value = filters.query;
  applyFilters();
}
function saveFilters() { writeStorage(filterStorageKey, JSON.stringify(filters)); }
root.addEventListener("change", event => {
  const select = event.target.closest("[data-filter]");
  if (!select) return;
  filters[select.dataset.filter] = select.value;
  saveFilters();
  animateLayout(applyFilters);
});
root.addEventListener("input", event => {
  if (event.target.id !== "session-search") return;
  filters.query = event.target.value;
  saveFilters();
  animateLayout(applyFilters);
});
root.addEventListener("click", event => {
  const clear = event.target.closest(".clear-filters");
  const state = event.target.closest("button[data-state]");
  const machine = event.target.closest("[data-location-choice]");
  if (clear) filters = { location: "", agent: "", surface: "", instance: "", state: "", query: "" };
  else if (state) filters.state = state.dataset.state;
  else if (machine) filters.location = filters.location === machine.dataset.locationChoice ? "" : machine.dataset.locationChoice;
  else return;
  saveFilters();
  animateLayout(restoreFilters);
});
function viewMode() { return readStorage(storageKey) === "board" ? "board" : "list"; }
function applyView(mode, animate = false) {
  document.body.dataset.view = mode;
  document.querySelectorAll("button[data-view]").forEach(button => button.setAttribute("aria-pressed", button.dataset.view === mode ? "true" : "false"));
  if (animate && !reducedMotion.matches) root.querySelector(mode === "board" ? ".kanban" : ".list").animate([
    { opacity: .3, transform: "translateY(4px)" }, { opacity: 1, transform: "none" }
  ], { duration: 220, easing: "cubic-bezier(.2,.8,.2,1)" });
}
document.addEventListener("click", event => {
  const button = event.target.closest("button[data-view]");
  if (!button) return;
  writeStorage(storageKey, button.dataset.view);
  applyView(button.dataset.view, true);
});
function applyTheme(theme) {
  document.body.dataset.theme = theme;
  document.documentElement.dataset.theme = theme;
  const button = document.querySelector(".theme-toggle");
  button.innerHTML = theme === "arcade" ? '<svg width="17" height="17" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.5" aria-hidden="true"><circle cx="12" cy="12" r="4"/><path d="M12 2v2m0 16v2M2 12h2m16 0h2M5 5l1.5 1.5m11 11L19 19M5 19l1.5-1.5m11-11L19 5"/></svg>' : '<svg width="17" height="17" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.5" aria-hidden="true"><path d="M21 13A9 9 0 0 1 11 3a9 9 0 1 0 10 10Z"/></svg>';
  button.setAttribute("aria-label", `Switch to ${theme === "arcade" ? "standard" : "arcade"} theme`);
}
document.querySelector(".theme-toggle").addEventListener("click", () => {
  const theme = document.body.dataset.theme === "arcade" ? "standard" : "arcade";
  writeStorage("cc-theme", theme);
  applyTheme(theme);
});
document.addEventListener("keydown", event => {
  const typing = event.target.matches("input, select, textarea") || event.target.isContentEditable;
  if (event.key === "/" && !typing && !event.ctrlKey && !event.metaKey && !event.altKey) {
    event.preventDefault(); root.querySelector("#session-search").focus();
  }
  if (event.key === "Escape" && event.target.id === "session-search") {
    filters.query = ""; saveFilters(); animateLayout(restoreFilters); event.target.blur();
  }
});
function patchHTML(current, next) {
  if (current && next && current.innerHTML !== next.innerHTML) current.innerHTML = next.innerHTML;
}
function syncRows(current, next) {
  const existing = new Map([...current.querySelectorAll(":scope > .session")].map(row => [row.dataset.key, row]));
  const desired = [...next.querySelectorAll(":scope > .session")];
  desired.forEach((incoming, index) => {
    let row = existing.get(incoming.dataset.key);
    if (row) {
      existing.delete(incoming.dataset.key);
      row.className = incoming.className;
      for (const key of ["state", "location", "agents", "surface", "instance", "search"]) row.dataset[key] = incoming.dataset[key];
      // Keep the actual disclosure and focused links while updating their text.
      for (const selector of [".title", ".repo-label", ".execution-label", ".line", ".session-context", ".session-status", ".project-symbol", ".meta", ".identity", ".body ul"]) patchHTML(row.querySelector(selector), incoming.querySelector(selector));
      row.querySelector(".project-symbol").className = incoming.querySelector(".project-symbol").className;
    } else row = incoming.cloneNode(true);
    const position = current.querySelectorAll(":scope > .session")[index];
    if (position !== row) current.insertBefore(row, position || current.querySelector(".empty"));
  });
  for (const row of existing.values()) row.remove();
}
function syncOptions(current, next) {
  const focused = document.activeElement === current;
  const value = current.value;
  const label = current.selectedOptions[0]?.textContent || value;
  // Native pickers remain intact while the user is choosing an option.
  if (!focused && current.innerHTML !== next.innerHTML) current.innerHTML = next.innerHTML;
  if (value && ![...current.options].some(option => option.value === value)) current.add(new Option(`${label.replace(/ \(no sessions\)$/, "")} (no sessions)`, value));
  current.value = value;
}
async function tick() {
  if (reading || document.hidden) return;
  reading = true;
  try {
    const response = await fetch("/partial", { cache: "no-store" });
    if (!response.ok) return;
    const html = await response.text();
    const parsed = new DOMParser().parseFromString(html, "text/html");
    const next = parsed.querySelector(".live");
    const current = root.querySelector(".live");
    if (!next || next.dataset.stamp === current.dataset.stamp) return;
    const y = window.scrollY;
    const focused = document.activeElement;
    animateLayout(() => {
      root.querySelectorAll("[data-filter]").forEach(select => syncOptions(select, parsed.querySelector(`#${select.id}`)));
      const machines = root.querySelector(".machines");
      const newMachines = parsed.querySelector(".machines");
      if (machines.dataset.render !== newMachines.dataset.render) {
        patchHTML(machines, newMachines); machines.dataset.render = newMachines.dataset.render;
      }
      patchHTML(root.querySelector(".alerts"), parsed.querySelector(".alerts"));
      patchHTML(root.querySelector(".read"), parsed.querySelector(".read"));
      patchHTML(root.querySelector(".overview"), parsed.querySelector(".overview"));
      syncRows(root.querySelector(".list"), parsed.querySelector(".list"));
      root.querySelectorAll(".column").forEach((column, index) => syncRows(column, parsed.querySelectorAll(".column")[index]));
      current.dataset.stamp = next.dataset.stamp;
      restoreFilters();
    });
    if (focused?.isConnected && document.activeElement !== focused && root.contains(focused)) focused.focus({ preventScroll: true });
    // Only correct a real layout shift; never cancel a user's smooth scroll.
    if (Math.abs(window.scrollY - y) > 2) window.scrollTo(0, y);
  } catch {} finally { reading = false; }
}
applyTheme(readStorage("cc-theme") === "standard" ? "standard" : "arcade");
applyView(viewMode());
restoreFilters();
setInterval(tick, 5000);
document.addEventListener("visibilitychange", () => { if (!document.hidden) tick(); });
