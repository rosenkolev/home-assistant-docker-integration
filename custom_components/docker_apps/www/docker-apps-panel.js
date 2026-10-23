// Docker Apps panel: apps, store, resources and live logs.
// Plain web component (no external libraries) so it works offline.
// cspell:words autorenew unsub

const DOMAIN = "docker_apps";
const REFRESH_MS = 5000;
const STORE_PAGE = 60;

const esc = (value) =>
  String(value ?? "").replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" })[c]);

const bytes = (value) => {
  if (value === null || value === undefined) return "–";
  const units = ["B", "KB", "MB", "GB", "TB"];
  let i = 0;
  let v = value;
  while (v >= 1024 && i < units.length - 1) {
    v /= 1024;
    i += 1;
  }
  return `${v.toFixed(i > 1 ? 1 : 0)} ${units[i]}`;
};

// server time minus browser time, set on every state load
let clockOffset = 0;

const since = (iso) => {
  if (!iso) return "–";
  const seconds = Math.max(0, (Date.now() + clockOffset - new Date(iso).getTime()) / 1000);
  if (seconds < 3600) return `${Math.floor(seconds / 60)} min`;
  if (seconds < 86400) return `${Math.floor(seconds / 3600)} h`;
  return `${Math.floor(seconds / 86400)} d`;
};

const appStatus = (app) => {
  if (app.busy) return ["busy", app.busy];
  const running = app.containers.filter((c) => c.state === "running");
  if (!running.length) return ["off", "Stopped"];
  if (running.some((c) => c.health === "unhealthy")) return ["bad", "Unhealthy"];
  if (running.some((c) => c.health === "starting")) return ["warn", "Starting"];
  if (running.length < app.containers.length) return ["warn", "Partly running"];
  return ["ok", "Running"];
};

const containerStatus = (c) => {
  if (c.state !== "running") return ["off", c.state];
  if (c.health === "unhealthy") return ["bad", "unhealthy"];
  if (c.health === "starting") return ["warn", "starting"];
  return ["ok", c.health || "running"];
};

const icon = (name) => `<ha-icon icon="mdi:${name}"></ha-icon>`;

// The app runs on the Docker host, reached at the same address as Home Assistant.
const webUrl = ([port, path]) => `http://${location.hostname}:${port}${path}`;

// Store descriptions and release notes are markdown; HA's own element renders them.
const markdown = (root, selector, text) => {
  const target = root.querySelector(selector);
  if (!target || !text) return;
  if (customElements.get("ha-markdown")) {
    const element = document.createElement("ha-markdown");
    element.breaks = true;
    element.content = text;
    target.replaceChildren(element);
  } else {
    target.textContent = text;
  }
};

const iconButton = (action, name, title, data = "", disabled = false) =>
  `<button class="icon-btn" title="${esc(title)}" aria-label="${esc(title)}" data-action="${action}" ${data} ${disabled ? "disabled" : ""}>${icon(name)}</button>`;

const STYLE = `
  :host { display: block; background: var(--primary-background-color); color: var(--primary-text-color);
    min-height: 100vh; font-family: var(--ha-font-family-body, var(--paper-font-body1_-_font-family, sans-serif)); }
  header { display: flex; align-items: center; gap: 12px; padding: 0 16px; height: 56px;
    background: var(--app-header-background-color, var(--primary-color)); color: var(--app-header-text-color, #fff); }
  header h1 { font-size: 20px; font-weight: 400; margin: 0; flex: 1; }
  header .host { font-size: 13px; opacity: .85; }
  nav { display: flex; gap: 4px; padding: 0 16px; border-bottom: 1px solid var(--divider-color);
    background: var(--card-background-color); overflow-x: auto; }
  nav button { background: none; border: none; padding: 14px 14px 12px; font: inherit; cursor: pointer; white-space: nowrap;
    color: var(--secondary-text-color); border-bottom: 2px solid transparent; }
  nav button.active { color: var(--primary-color); border-bottom-color: var(--primary-color); }
  main { padding: 16px; max-width: 1400px; margin: 0 auto; box-sizing: border-box; }

  .tiles { display: grid; gap: 12px; grid-template-columns: repeat(auto-fit, minmax(150px, 1fr)); margin-bottom: 16px; }
  .tile { background: var(--card-background-color); border: 1px solid var(--divider-color);
    border-radius: var(--ha-card-border-radius, 12px); padding: 12px 14px; }
  .tile .value { font-size: 22px; font-weight: 500; }
  .tile .label { font-size: 12px; color: var(--secondary-text-color); text-transform: uppercase; letter-spacing: .04em; }
  .tile.accent .value { color: var(--primary-color); }

  .toolbar { display: flex; gap: 8px; align-items: center; flex-wrap: wrap; margin-bottom: 16px; }
  input, select { padding: 8px 10px; border-radius: 8px; font: inherit; box-sizing: border-box;
    border: 1px solid var(--divider-color); background: var(--card-background-color); color: inherit; }
  .toolbar input:not([type="checkbox"]) { flex: 1; min-width: 160px; }

  .grid { display: grid; gap: 16px; grid-template-columns: repeat(auto-fill, minmax(360px, 1fr)); align-items: start; }
  .card { background: var(--card-background-color); border-radius: var(--ha-card-border-radius, 12px);
    box-shadow: var(--ha-card-box-shadow, none); border: 1px solid var(--divider-color); box-sizing: border-box; min-width: 0; }
  .card > .section { padding: 14px 16px; }
  .card > .section + .section { border-top: 1px solid var(--divider-color); }

  .row { display: flex; align-items: center; gap: 12px; min-width: 0; }
  .grow { flex: 1; min-width: 0; }
  .name { font-weight: 500; font-size: 16px; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
  .muted { color: var(--secondary-text-color); font-size: 13px; }
  .ellipsis { overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
  .app-icon { width: 44px; height: 44px; border-radius: 10px; object-fit: contain; flex: none;
    background: var(--secondary-background-color); display: grid; place-items: center; color: var(--secondary-text-color); }

  .chip { font-size: 12px; padding: 3px 10px; border-radius: 12px; white-space: nowrap; display: inline-flex; align-items: center; gap: 4px; }
  .chip.ok { background: rgba(67,160,71,.14); color: var(--success-color, #43a047); }
  .chip.warn, .chip.busy { background: rgba(255,152,0,.16); color: var(--warning-color, #ef8a00); }
  .chip.bad { background: rgba(219,68,55,.14); color: var(--error-color, #db4437); }
  .chip.off { background: var(--secondary-background-color); color: var(--secondary-text-color); }
  .chip.info { background: rgba(3,169,244,.12); color: var(--primary-color); }
  button.chip { border: none; cursor: pointer; font: inherit; font-size: 12px; }
  .dot { width: 8px; height: 8px; border-radius: 50%; flex: none; background: var(--secondary-text-color); }
  .dot.ok { background: var(--success-color, #43a047); }
  .dot.warn { background: var(--warning-color, #ef8a00); }
  .dot.bad { background: var(--error-color, #db4437); }

  .metrics { display: flex; gap: 8px; flex-wrap: wrap; margin-top: 12px; }
  .metric { display: inline-flex; align-items: center; gap: 6px; font-size: 13px; padding: 4px 10px;
    border-radius: 8px; background: var(--secondary-background-color); }
  .metric ha-icon { --mdc-icon-size: 16px; color: var(--secondary-text-color); }

  .banner { display: flex; align-items: center; gap: 12px; margin-top: 12px; padding: 10px 12px; border-radius: 10px;
    background: rgba(3,169,244,.10); }
  .banner ha-icon { color: var(--primary-color); }
  .banner.hold { background: rgba(255,152,0,.12); }
  .banner.hold ha-icon { color: var(--warning-color, #ef8a00); }
  .banner.error { background: rgba(219,68,55,.10); }
  .banner .title { font-weight: 500; font-size: 14px; }

  button.btn { font: inherit; font-size: 14px; padding: 7px 14px; border-radius: 8px; cursor: pointer; white-space: nowrap;
    border: 1px solid var(--divider-color); background: transparent; color: var(--primary-text-color); }
  button.btn.primary { background: var(--primary-color); color: var(--text-primary-color, #fff); border-color: var(--primary-color); }
  button.btn.danger { color: var(--error-color, #db4437); }
  button.btn.primary.danger { background: var(--error-color, #db4437); border-color: var(--error-color, #db4437);
    color: #fff; }
  button:disabled { opacity: .45; cursor: default; }
  .icon-btn { width: 36px; height: 36px; border-radius: 50%; border: none; background: transparent; cursor: pointer;
    color: var(--secondary-text-color); display: inline-grid; place-items: center; flex: none; }
  .icon-btn:hover:not(:disabled) { background: var(--secondary-background-color); color: var(--primary-text-color); }
  .icon-btn.small { width: 30px; height: 30px; }
  .icon-btn.small ha-icon { --mdc-icon-size: 18px; }

  .containers-toggle { display: flex; align-items: center; gap: 6px; width: 100%; padding: 0; border: none;
    background: none; font: inherit; font-size: 13px; color: var(--secondary-text-color); cursor: pointer; }
  .containers-toggle ha-icon { --mdc-icon-size: 18px; transition: transform .15s; }
  .containers-toggle.open ha-icon { transform: rotate(90deg); }
  .container { display: grid; grid-template-columns: auto minmax(0, 1fr) auto; gap: 4px 10px; align-items: center;
    padding: 10px 0; border-top: 1px solid var(--divider-color); }
  .container:first-of-type { margin-top: 8px; }
  .container .info { min-width: 0; }
  .container .line { display: flex; gap: 8px; align-items: center; flex-wrap: wrap; font-size: 12px; color: var(--secondary-text-color); }
  .container .tools { display: flex; align-items: center; gap: 2px; }

  .footer { display: flex; align-items: center; gap: 4px; padding: 6px 8px 6px 16px; }
  .toggle { display: inline-flex; align-items: center; gap: 8px; font-size: 14px; cursor: pointer; user-select: none; }
  .toggle input { width: 34px; height: 20px; appearance: none; border-radius: 10px; background: var(--disabled-color, #bdbdbd);
    position: relative; cursor: pointer; transition: background .15s; margin: 0; padding: 0; border: none; }
  .toggle input::after { content: ""; position: absolute; top: 2px; left: 2px; width: 16px; height: 16px; border-radius: 50%;
    background: #fff; transition: left .15s; }
  .toggle input:checked { background: var(--primary-color); }
  .toggle input:checked::after { left: 16px; }
  .toggle input:disabled { opacity: .4; }
  .menu-wrap { position: relative; }
  .menu { position: absolute; right: 0; bottom: 40px; z-index: 5; min-width: 200px; padding: 6px 0;
    background: var(--card-background-color); border: 1px solid var(--divider-color); border-radius: 10px;
    box-shadow: 0 6px 24px rgba(0,0,0,.18); }
  .menu button { display: flex; align-items: center; gap: 12px; width: 100%; padding: 10px 16px; border: none; background: none;
    font: inherit; font-size: 14px; color: inherit; cursor: pointer; text-align: left; }
  .menu button:hover:not(:disabled) { background: var(--secondary-background-color); }
  .menu button ha-icon { --mdc-icon-size: 20px; color: var(--secondary-text-color); }
  .menu button.danger, .menu button.danger ha-icon { color: var(--error-color, #db4437); }
  progress { width: 100%; height: 4px; margin-top: 12px; accent-color: var(--primary-color); }

  table { width: 100%; border-collapse: collapse; font-size: 14px; }
  th, td { text-align: left; padding: 8px; border-bottom: 1px solid var(--divider-color); vertical-align: middle; }
  th { color: var(--secondary-text-color); font-weight: 500; font-size: 12px; text-transform: uppercase; letter-spacing: .04em; }
  .scroll { overflow-x: auto; }
  code, .mono { font-family: var(--ha-font-family-code, monospace); font-size: 13px; }

  dialog { border: none; border-radius: 16px; padding: 0; width: min(640px, calc(100vw - 32px));
    background: var(--card-background-color); color: var(--primary-text-color); box-shadow: 0 12px 48px rgba(0,0,0,.3); }
  dialog.wide { width: min(1000px, calc(100vw - 32px)); }
  dialog::backdrop { background: rgba(0,0,0,.5); }
  dialog .head { display: flex; align-items: center; gap: 12px; padding: 18px 20px 8px; }
  dialog .head .name { font-size: 20px; }
  dialog .body { padding: 8px 20px 16px; max-height: 70vh; overflow: auto; }
  dialog .foot { display: flex; gap: 8px; justify-content: flex-end; padding: 12px 20px 18px; }
  dialog label { display: block; margin: 14px 0 6px; font-size: 13px; color: var(--secondary-text-color); }
  dialog input, dialog select { width: 100%; }
  dialog input:disabled, dialog select:disabled { opacity: .6; }
  dialog input.invalid { border-color: var(--error-color, #db4437); }
  dialog label.toggle { display: flex; color: inherit; font-size: 14px; margin: 16px 0 0; }
  dialog .toggle input { width: 34px; }
  dialog h3 { font-size: 13px; font-weight: 500; color: var(--secondary-text-color); text-transform: uppercase;
    letter-spacing: .04em; margin: 18px 0 6px; }
  .versions { display: flex; align-items: center; gap: 10px; font-size: 18px; margin: 4px 0 8px; flex-wrap: wrap; }
  .versions .mono { font-size: 18px; }
  .versions .from { color: var(--secondary-text-color); }
  .versions .to { color: var(--primary-color); font-weight: 500; }
  .md { font-size: 14px; line-height: 1.5; white-space: pre-wrap; max-height: 240px; overflow: auto;
    padding: 10px 12px; border-radius: 10px; background: var(--secondary-background-color); }
  .md ha-markdown { white-space: normal; }
  .steps { margin: 0; padding-left: 20px; font-size: 14px; color: var(--secondary-text-color); line-height: 1.7; }
  pre.logs { margin: 0; font-size: 12px; line-height: 1.45; white-space: pre-wrap; word-break: break-all;
    background: #121212; color: #ddd; padding: 12px; border-radius: 10px; min-height: 320px; max-height: 55vh; overflow: auto; }
  .notice { display: flex; gap: 10px; align-items: flex-start; padding: 12px 14px; border-radius: 10px; margin-bottom: 12px; font-size: 14px;
    background: rgba(255,152,0,.12); }
  .empty { padding: 48px 16px; text-align: center; color: var(--secondary-text-color); }
  a { color: var(--primary-color); }
  @media (max-width: 600px) { main { padding: 12px; } .grid { grid-template-columns: 1fr; } header .host { display: none; } }
`;

class DockerAppsPanel extends HTMLElement {
  constructor() {
    super();
    this.attachShadow({ mode: "open" });
    this._tab = "apps";
    this._filter = "";
    this._category = "";
    this._state = null;
    this._store = null;
    this._resources = null;
    this._open = new Set();
    this._menu = null;
    this._error = null;
    this._shown = STORE_PAGE;
  }

  set hass(hass) {
    const first = !this._hass;
    this._hass = hass;
    if (first) {
      this._renderShell();
      this._load();
    }
  }

  set narrow(value) {
    this._narrow = value;
    const menu = this.shadowRoot.querySelector("ha-menu-button");
    if (menu) menu.narrow = value;
  }

  connectedCallback() {
    this._timer = setInterval(() => {
      if (document.visibilityState === "visible" && this._tab === "apps" && !this._dialogOpen() && !this._menu) this._load();
    }, REFRESH_MS);
  }

  disconnectedCallback() {
    clearInterval(this._timer);
    this._stopLogs();
  }

  _dialogOpen() {
    return this.shadowRoot.querySelector("dialog[open]") !== null;
  }

  async _ws(type, extra = {}) {
    return this._hass.connection.sendMessagePromise({ type: `${DOMAIN}/${type}`, ...extra });
  }

  async _call(service, data, response = false) {
    try {
      const result = await this._hass.callService(DOMAIN, service, data, undefined, true, response);
      return response ? result?.response : result;
    } catch (err) {
      this._toast(err.message || String(err));
      throw err;
    }
  }

  _toast(message) {
    this.dispatchEvent(new CustomEvent("hass-notification", { detail: { message }, bubbles: true, composed: true }));
  }

  async _load() {
    try {
      const needState = this._tab !== "store" || !this._state;
      const [state, extra] = await Promise.all([
        needState ? this._ws("state") : this._state,
        this._tab === "store" ? this._ws("store") : this._tab === "resources" ? this._ws("resources") : null,
      ]);
      this._state = state;
      if (state.now) clockOffset = new Date(state.now).getTime() - Date.now();
      if (this._tab === "store") this._store = extra;
      if (this._tab === "resources") this._resources = extra;
      this._error = null;
    } catch (err) {
      this._error = err.message || String(err);
    }
    this._renderMain();
  }

  _renderShell() {
    this.shadowRoot.innerHTML = `
      <style>${STYLE}</style>
      <header><ha-menu-button></ha-menu-button><h1>Apps</h1><span class="host"></span></header>
      <nav>
        <button data-tab="apps" class="active">Installed</button>
        <button data-tab="store">App store</button>
        <button data-tab="resources">Images &amp; volumes</button>
      </nav>
      <main></main>
      <dialog id="dialog"></dialog>`;
    const menu = this.shadowRoot.querySelector("ha-menu-button");
    menu.hass = this._hass;
    menu.narrow = this._narrow;
    this.shadowRoot.querySelector("nav").addEventListener("click", (ev) => {
      const tab = ev.target.dataset?.tab;
      if (tab) this._setTab(tab);
    });
    const main = this.shadowRoot.querySelector("main");
    main.addEventListener("click", (ev) => this._onClick(ev));
    main.addEventListener("input", (ev) => this._onInput(ev));
    main.addEventListener("change", (ev) => this._onInput(ev));
  }

  _setTab(tab) {
    this._tab = tab;
    this._filter = "";
    this._menu = null;
    this.shadowRoot.querySelectorAll("nav button").forEach((b) => b.classList.toggle("active", b.dataset.tab === tab));
    this.shadowRoot.querySelector("main").innerHTML = `<div class="empty">Loading…</div>`;
    this._load();
  }

  _renderMain() {
    const main = this.shadowRoot.querySelector("main");
    const host = this._state?.host;
    this.shadowRoot.querySelector(".host").textContent = host ? `${host.name} · Docker ${host.version} · ${host.arch}` : "";
    if (this._error) {
      main.innerHTML = `<div class="notice">${icon("alert-outline")}<div>${esc(this._error)}</div></div>`;
      return;
    }
    const focused = this.shadowRoot.activeElement;
    const focusId = focused?.id;
    const caret = focused?.selectionStart;
    const render = { apps: () => this._renderApps(), store: () => this._renderStore(), resources: () => this._renderResources() };
    main.innerHTML = render[this._tab]();
    if (focusId) {
      const el = this.shadowRoot.getElementById(focusId);
      el?.focus();
      if (caret !== undefined && el?.setSelectionRange) el.setSelectionRange(caret, caret);
    }
  }

  // --- installed apps ---

  _renderApps() {
    const state = this._state;
    if (!state) return `<div class="empty">Loading…</div>`;
    const notices = [];
    if (state.read_only) notices.push("Read-only mode: actions are turned off in the integration options.");
    if (state.mounted && state.apps.some((a) => a.compose) && !state.apps.some((a) => a.editable)) {
      notices.push(`The apps folder isn't mounted into Home Assistant, so updates are off. Mount <code>${esc(state.apps_root)}</code> at the same path.`);
    }
    const filter = this._filter.toLowerCase();
    const apps = state.apps.filter((a) => !filter || a.name.toLowerCase().includes(filter));
    const updates = state.apps.filter((a) => a.update?.available).length;
    const managed = state.apps.filter((a) => a.managed).length;
    const tile = (value, label, accent = false) =>
      `<div class="tile ${accent ? "accent" : ""}"><div class="value">${value}</div><div class="label">${label}</div></div>`;
    return `
      ${notices.map((n) => `<div class="notice">${icon("information-outline")}<div>${n}</div></div>`).join("")}
      <div class="tiles">
        ${tile(state.apps.length, "Apps")}
        ${tile(`${state.host.running}<span class="muted"> / ${state.host.containers}</span>`, "Containers running")}
        ${tile(managed, "Managed")}
        ${tile(updates, "Updates", updates > 0)}
        ${tile(bytes((state.disk?.images_reclaimable || 0) + (state.disk?.volumes_reclaimable || 0)), "Reclaimable")}
      </div>
      <div class="toolbar">
        <input id="filter" placeholder="Search apps" value="${esc(this._filter)}" data-input="filter">
        <button class="btn" data-action="check-updates">${icon("refresh")} Check for updates</button>
      </div>
      <div class="grid">${apps.map((a) => this._renderApp(a, state.read_only)).join("") || `<div class="empty">No apps</div>`}</div>`;
  }

  _renderApp(app, readOnly) {
    const [cls, label] = appStatus(app);
    const cpu = app.containers.reduce((s, c) => s + (c.cpu || 0), 0);
    const mem = app.containers.reduce((s, c) => s + (c.memory || 0), 0);
    const started = app.containers.map((c) => c.started).filter(Boolean).sort()[0];
    const update = app.update;
    const k = esc(app.key);
    const data = `data-app="${k}"`;
    const isSelf = app.containers.some((c) => c.is_self);
    const version = update?.installed || app.version || "";
    const appIcon = app.icon
      ? `<img class="app-icon" src="${esc(app.icon)}" alt="">`
      : `<div class="app-icon">${icon(isSelf ? "home-assistant" : app.compose ? "layers-outline" : "docker")}</div>`;

    let banner = "";
    if (update?.blocked) {
      banner = `<div class="banner hold">${icon("pause-circle-outline")}<div class="grow"><div class="title">Update on hold</div>
        <div class="muted">${esc(update.blocked)}</div></div></div>`;
    } else if (update?.available) {
      banner = `<div class="banner">${icon("arrow-up-circle-outline")}<div class="grow">
          <div class="title">Update available: ${esc(update.latest)}</div>
          <div class="muted">${update.store ? "New app package" : `${update.changes.length} service${update.changes.length === 1 ? "" : "s"} change`}</div></div>
        ${readOnly ? "" : `<button class="btn primary" data-action="update" ${data} ${app.busy ? "disabled" : ""}>Update</button>`}</div>`;
    } else if (update?.error) {
      banner = `<div class="banner error">${icon("alert-circle-outline")}<div class="grow muted">Update check failed: ${esc(update.error)}</div></div>`;
    }

    const menuItems = [
      ["launcher", app.launcher ? "checkbox-marked-outline" : "checkbox-blank-outline", "Show in app launcher"],
      app.compose && app.editable ? ["recreate", "autorenew", "Recreate"] : null,
      app.history?.length && app.editable ? ["rollback", "history", "Roll back…"] : null,
      app.managed && !app.store ? ["settings", "tune-variant", "Update settings…"] : null,
      isSelf ? null : ["uninstall", "delete-outline", "Remove…", "danger"],
    ].filter(Boolean);
    const running = cls !== "off";
    const footer = `
      <div class="section footer">
        ${readOnly || !app.compose ? `<span class="grow"></span>` : `<label class="toggle grow" title="Check for updates and allow one-click updates">
          <input type="checkbox" data-input="managed" ${data} ${app.managed ? "checked" : ""} ${app.editable || app.store ? "" : "disabled"}> Managed</label>`}
        ${app.web ? `<a class="icon-btn" href="${esc(webUrl(app.web))}" target="_blank" rel="noopener" title="Open web UI" aria-label="Open web UI">${icon("open-in-new")}</a>` : ""}
        ${iconButton("logs", "text-box-outline", "Logs", data)}
        ${readOnly ? "" : iconButton("restart", "restart", "Restart", data, app.busy)}
        ${readOnly || isSelf ? "" : running ? iconButton("stop", "stop", "Stop", data, app.busy) : iconButton("start", "play", "Start", data, app.busy)}
        ${readOnly || !menuItems.length ? "" : `<div class="menu-wrap">
          ${iconButton("menu", "dots-vertical", "More", data)}
          ${this._menu === app.key ? `<div class="menu">${menuItems
            .map(([action, name, text, style]) => `<button class="${style || ""}" data-action="${action}" ${data}>${icon(name)}${text}</button>`)
            .join("")}</div>` : ""}
        </div>`}
      </div>`;

    const open = this._open.has(app.key);
    return `
      <div class="card">
        <div class="section">
          <div class="row">
            ${appIcon}
            <div class="grow">
              <div class="name">${esc(app.name)}</div>
              <div class="muted ellipsis">${esc(version)}</div>
            </div>
            <span class="chip ${cls}">${esc(label)}</span>
          </div>
          ${app.busy ? `<progress ${app.progress == null ? "" : `value="${app.progress}" max="100"`}></progress>` : ""}
          ${banner}
          <div class="metrics">
            <span class="metric" title="CPU">${icon("cpu-64-bit")}${cpu.toFixed(1)}%</span>
            <span class="metric" title="Memory">${icon("memory")}${bytes(mem)}</span>
            <span class="metric" title="Up since">${icon("clock-outline")}${since(started)}</span>
          </div>
        </div>
        <div class="section">
          <button class="containers-toggle ${open ? "open" : ""}" data-action="toggle" ${data}>
            ${icon("chevron-right")} ${app.containers.length} container${app.containers.length === 1 ? "" : "s"}
          </button>
          ${open ? app.containers.map((c) => this._renderContainer(app, c, readOnly)).join("") : ""}
        </div>
        ${footer}
      </div>`;
  }

  _renderContainer(app, c, readOnly) {
    const [cls, label] = containerStatus(c);
    const canTrack = app.managed && !readOnly && !app.store && c.service;
    const track = canTrack
      ? `<button class="chip ${c.tracked ? "info" : "off"}" data-action="track" data-app="${esc(app.key)}" data-service="${esc(c.service)}"
           title="${c.tracked ? "Updated with the app. Click to stop tracking." : "Not updated with the app. Click to track."}">
           ${icon(c.tracked ? "check" : "minus")}${c.tracked ? "Tracked" : "Not tracked"}</button>`
      : "";
    const newer = c.latest
      ? `<span class="chip ${c.required ? "warn" : "info"}">${icon("arrow-up")}${esc(c.latest)}${c.required ? " required" : ""}</span>`
      : "";
    const updateOne = c.latest && !c.tracked && !readOnly && app.editable
      ? iconButton("update-service", "arrow-up-bold-circle-outline", `Update ${c.service} to ${c.latest}`, `data-app="${esc(app.key)}" data-service="${esc(c.service)}" data-tag="${esc(c.latest)}"`)
      : "";
    return `
      <div class="container">
        <span class="dot ${cls}" title="${esc(label)}"></span>
        <div class="info">
          <div class="ellipsis"><b>${esc(c.service || c.name)}</b> <span class="muted mono">${esc(c.image)}</span></div>
          <div class="line">
            <span>${esc(label)}</span>
            <span>${c.cpu == null ? "–" : `${c.cpu.toFixed(1)}%`} CPU</span>
            <span>${bytes(c.memory)}</span>
            <span>up ${since(c.started)}</span>
            ${c.restarts ? `<span>${c.restarts} restarts</span>` : ""}
            ${track}${newer}
          </div>
        </div>
        <div class="tools">
          ${updateOne}
          ${iconButton("container-logs", "text-box-outline", "Logs", `data-container="${esc(c.key)}"`).replace("icon-btn", "icon-btn small")}
          ${readOnly || c.is_self ? "" : iconButton("container-restart", "restart", "Restart", `data-container="${esc(c.key)}"`).replace("icon-btn", "icon-btn small")}
        </div>
      </div>`;
  }

  // --- store ---

  _renderStore() {
    const store = this._store;
    if (!store) return `<div class="empty">Loading…</div>`;
    if (!store.repositories.length) {
      return `<div class="empty">${icon("store-outline")}<p>No app store repositories yet.</p>
        <p>Add one in <a href="/config/integrations/integration/${DOMAIN}">Settings › Devices &amp; services › Docker Apps</a> › <b>Add app store repository</b>.</p></div>`;
    }
    const filter = this._filter.toLowerCase();
    const categories = [...new Set(store.apps.flatMap((a) => a.categories))].sort();
    const apps = store.apps.filter(
      (a) =>
        (!filter || a.name.toLowerCase().includes(filter) || (a.tagline || "").toLowerCase().includes(filter)) &&
        (!this._category || a.categories.includes(this._category)),
    );
    return `
      ${store.repositories.filter((r) => r.error).map((r) => `<div class="notice">${icon("alert-outline")}<div>${esc(r.name)}: ${esc(r.error)}</div></div>`).join("")}
      <div class="toolbar">
        <input id="filter" placeholder="Search ${store.apps.length} apps" value="${esc(this._filter)}" data-input="filter">
        <select data-input="category" id="category"><option value="">All categories</option>
          ${categories.map((c) => `<option ${c === this._category ? "selected" : ""}>${esc(c)}</option>`).join("")}</select>
        <button class="btn" data-action="refresh-store">${icon("refresh")} Refresh</button>
      </div>
      <div class="grid">${apps.slice(0, this._shown).map((a) => this._renderStoreApp(a)).join("") || `<div class="empty">No apps found</div>`}</div>
      ${apps.length > this._shown ? `<div class="empty"><button class="btn" data-action="more">Show more (${apps.length - this._shown} left)</button></div>` : ""}`;
  }

  _renderStoreApp(app) {
    const repo = this._store.repositories.find((r) => r.id === app.repo_id);
    const appIcon = app.icon ? `<img class="app-icon" loading="lazy" src="${esc(app.icon)}" alt="">` : `<div class="app-icon">${icon("package-variant")}</div>`;
    let action = `<button class="btn primary" data-action="install" data-store="${esc(app.id)}">Install</button>`;
    if (app.installed) action = `<span class="chip ok">${icon("check")}Installed</span>`;
    else if (!app.supported) action = `<span class="chip off">Not for ${esc(this._state?.host?.arch || "this CPU")}</span>`;
    else if (this._state?.read_only) action = "";
    return `
      <div class="card"><div class="section">
        <div class="row">${appIcon}
          <div class="grow"><div class="name">${esc(app.name)}</div>
          <div class="muted ellipsis">${esc(app.version)} · ${esc(repo?.name || "")}</div></div>
          ${action}
        </div>
        <div class="muted" style="margin-top:10px">${esc(app.tagline)}</div>
      </div></div>`;
  }

  // --- resources ---

  _renderResources() {
    const res = this._resources;
    const disk = this._state?.disk;
    if (!res) return `<div class="empty">Loading…</div>`;
    const readOnly = this._state?.read_only;
    const prune = (kind, text) => (readOnly ? "" : `<button class="btn" data-action="prune" data-kind="${kind}">${text}</button>`);
    const table = (title, headers, rows) => `
      <div class="card" style="margin-bottom:16px"><div class="section"><div class="name">${title}</div></div>
        <div class="scroll"><table><tr>${headers.map((h) => `<th>${h}</th>`).join("")}</tr>${rows}</table></div></div>`;
    return `
      <div class="tiles">
        <div class="tile"><div class="value">${bytes(disk?.images_size)}</div><div class="label">Images · ${disk?.images_unused ?? "–"} unused</div></div>
        <div class="tile"><div class="value">${bytes(disk?.volumes_size)}</div><div class="label">Volumes · ${disk?.volumes_unused ?? "–"} unused</div></div>
        <div class="tile"><div class="value">${bytes(disk?.build_cache_size)}</div><div class="label">Build cache</div></div>
        <div class="tile accent"><div class="value">${bytes((disk?.images_reclaimable || 0) + (disk?.volumes_reclaimable || 0))}</div><div class="label">Reclaimable</div></div>
      </div>
      <div class="toolbar">
        ${prune("containers", "Remove stopped containers")}
        ${prune("images", "Remove dangling images")}
        ${prune("images_unused", "Remove all unused images")}
        ${prune("networks", "Remove unused networks")}
        ${prune("build_cache", "Clear build cache")}
        ${prune("volumes", "Remove unused volumes")}
      </div>
      ${table(
        "Images",
        ["Tag", "Size", "Created", "Used"],
        res.images
          .sort((a, b) => b.size - a.size)
          .map((i) => `<tr><td class="mono">${esc(i.tags.join(", ") || i.id.slice(7, 19))}</td><td>${bytes(i.size)}</td>
            <td>${i.created ? new Date(i.created * 1000).toLocaleDateString() : "–"}</td>
            <td>${i.in_use ? `<span class="chip ok">in use</span>` : `<span class="chip off">unused</span>`}</td></tr>`)
          .join(""),
      )}
      ${table("Volumes", ["Name", "App", "Driver"], res.volumes.map((v) => `<tr><td class="mono">${esc(v.name)}</td><td>${esc(v.project || "–")}</td><td>${esc(v.driver)}</td></tr>`).join(""))}
      ${table("Networks", ["Name", "App", "Driver"], res.networks.map((n) => `<tr><td class="mono">${esc(n.name)}</td><td>${esc(n.project || "–")}</td><td>${esc(n.driver)}</td></tr>`).join(""))}`;
  }

  // --- events ---

  _onInput(ev) {
    const { input, app } = ev.target.dataset || {};
    if (input === "filter") {
      this._filter = ev.target.value;
      this._shown = STORE_PAGE;
      this._renderMain();
    } else if (input === "category") {
      this._category = ev.target.value;
      this._shown = STORE_PAGE;
      this._renderMain();
    } else if (input === "managed" && ev.type === "change") {
      const managed = ev.target.checked;
      this._call("manage", { app, managed })
        .then(() => this._toast(managed ? "Managed: checking for updates…" : "Not managed anymore"))
        .catch(() => {})
        .finally(() => this._load());
    }
  }

  async _onClick(ev) {
    const button = ev.target.closest("[data-action]");
    if (!button || button.disabled) {
      if (this._menu && !ev.target.closest(".menu")) {
        this._menu = null;
        this._renderMain();
      }
      return;
    }
    const { action, app, container, kind, store, service, tag } = button.dataset;
    const appInfo = this._state?.apps.find((a) => a.key === app);
    if (action === "toggle") {
      this._open.has(app) ? this._open.delete(app) : this._open.add(app);
      this._renderMain();
      return;
    }
    if (action === "more") {
      this._shown += STORE_PAGE;
      this._renderMain();
      return;
    }
    if (action === "menu") {
      this._menu = this._menu === app ? null : app;
      this._renderMain();
      return;
    }
    if (this._menu) {
      this._menu = null;
      this._renderMain();
    }
    try {
      switch (action) {
        case "check-updates":
          button.disabled = true;
          await this._call("check_updates", { repositories: false });
          break;
        case "refresh-store":
          button.disabled = true;
          await this._call("check_updates", { repositories: true });
          break;
        case "update":
          if (await this._confirmUpdate(appInfo)) await this._callEntity("update", "install", appInfo);
          break;
        case "rollback": {
          const version = await this._pick(`Roll back ${appInfo.name}`, "Bring back the compose files of an earlier version.", appInfo.history);
          if (version) await this._callEntity("update", "install", appInfo, { version });
          break;
        }
        case "restart":
        case "start":
        case "stop":
          if (action === "stop" && !(await this._confirm(`Stop ${appInfo.name}?`, "All containers of the app stop.", "Stop"))) break;
          for (const c of appInfo.containers) await this._call(`container_${action}`, { container: c.key });
          break;
        case "recreate":
          if (await this._confirm(`Recreate ${appInfo.name}?`, "All containers are created again from the compose files. Data in volumes stays.", "Recreate")) {
            await this._call("recreate", { app, pull: false });
          }
          break;
        case "uninstall": {
          const answer = await this._confirm(
            `Remove ${appInfo.name}?`,
            appInfo.store
              ? `<div>The containers and networks of the app are removed.</div>
                 <label class="toggle"><input type="checkbox" id="remove-data"> Also delete the app folder and its data</label>`
              : `<div>The containers and networks of the app are removed.</div>
                 <div class="muted" style="margin-top:8px">The compose files and volumes stay, so it can be started again with <code>docker compose up -d</code>.</div>`,
            "Remove",
            "danger",
          );
          if (answer) await this._call("uninstall", { app, remove_data: answer === "checked" });
          break;
        }
        case "logs":
          this._showLogs(appInfo.name, appInfo.containers.map((c) => c.key));
          break;
        case "container-logs":
          this._showLogs(container, [container]);
          break;
        case "container-restart":
          await this._call("container_restart", { container });
          break;
        case "settings":
          await this._settings(appInfo);
          break;
        case "launcher":
          await this._call("manage", { app, launcher: !appInfo.launcher });
          break;
        case "track": {
          const services = appInfo.containers
            .filter((c) => c.service && (c.service === service ? !c.tracked : c.tracked))
            .map((c) => c.service);
          await this._call("manage", { app, managed: true, services });
          break;
        }
        case "update-service":
          if (await this._confirm(`Update ${service}?`, `<div class="versions"><span class="mono">${esc(service)}</span> → <span class="to">${esc(tag)}</span></div>
              Only this service of ${esc(appInfo.name)} changes. If it's a database, back it up first.`, "Update")) {
            await this._call("update_services", { app, services: [service], tag }, true);
          }
          break;
        case "prune":
          if (await this._confirm("Remove unused resources?", `${esc(button.textContent)}. This can't be undone.`, "Remove", "danger")) {
            const result = await this._call("prune", { kind }, true);
            this._toast(`Removed ${result?.removed ?? 0} items, freed ${bytes(result?.space_reclaimed)}`);
          }
          break;
        case "install":
          await this._install(store);
          break;
        default:
          break;
      }
    } catch (err) {
      // already shown as a notification
    }
    this._load();
  }

  async _callEntity(domain, service, app, extra = {}) {
    const entities = await this._hass.callWS({ type: "config/entity_registry/list_for_display" });
    const entity = entities.entities.find((e) => e.di === app.device_id && e.ei.startsWith(`${domain}.`));
    if (!entity) throw new Error("Update entity not found");
    await this._hass.callService(domain, service, { entity_id: entity.ei, ...extra });
  }

  // --- dialogs ---

  _dialog(title, body, buttons, wide = false) {
    const dialog = this.shadowRoot.getElementById("dialog");
    // one dialog element is reused: drop the handlers of the previous dialog
    dialog.onclick = dialog.oninput = dialog.oncancel = dialog.onclose = null;
    dialog.className = wide ? "wide" : "";
    dialog.innerHTML = `<div class="head"><div class="name grow">${esc(title)}</div>
        <button class="icon-btn" data-r="close" aria-label="Close">${icon("close")}</button></div>
      <div class="body">${body}</div>
      <div class="foot">${buttons}</div>`;
    dialog.showModal();
    return dialog;
  }

  _ask(dialog, onYes) {
    return new Promise((resolve) => {
      dialog.onclick = (ev) => {
        const r = ev.target.closest("[data-r]")?.dataset.r;
        if (!r) return;
        const value = r === "yes" ? onYes() : null;
        dialog.close();
        resolve(value);
      };
      dialog.oncancel = () => resolve(null);
    });
  }

  _confirm(title, body, label, style = "primary") {
    const dialog = this._dialog(
      title,
      `<div style="font-size:14px;line-height:1.5">${body}</div>`,
      `<button class="btn" data-r="no">Cancel</button><button class="btn ${style === "danger" ? "primary danger" : "primary"}" data-r="yes">${esc(label)}</button>`,
    );
    return this._ask(dialog, () => (dialog.querySelector("#remove-data")?.checked ? "checked" : true));
  }

  _confirmUpdate(app) {
    const update = app.update;
    const changes = update.changes
      .map(
        (s) => `<tr><td><b>${esc(s.service)}</b>${s.required ? ` <span class="chip warn">required</span>` : ""}<div class="muted mono">${esc(s.image)}</div></td>
          <td class="mono">${esc(s.current)}</td><td>→</td><td class="mono to"><b>${esc(s.latest)}</b></td></tr>`,
      )
      .join("");
    const kept = update.kept
      .map((s) => `<li><b>${esc(s.service)}</b> <span class="muted mono">${esc(s.image)}</span>${s.latest ? ` <span class="chip info">${esc(s.latest)} available</span>` : ""}</li>`)
      .join("");
    const body = `
      <div class="versions"><span class="from mono">${esc(update.installed)}</span>${icon("arrow-right")}<span class="to mono">${esc(update.latest)}</span></div>
      ${update.store && update.release_notes ? `<h3>Release notes</h3><div class="md" id="release-notes"></div>` : ""}
      ${changes ? `<h3>What changes</h3><div class="scroll"><table><tr><th>Service</th><th>Now</th><th></th><th>New</th></tr>${changes}</table></div>` : ""}
      ${update.store && !changes ? `<h3>What changes</h3><div class="muted">The app's compose file is replaced with the new package. Your <code>compose.override.yaml</code> and settings stay.</div>` : ""}
      ${kept ? `<h3>Stays as it is</h3><ul style="margin:0;padding-left:20px;line-height:1.8;font-size:14px">${kept}</ul>` : ""}
      <h3>What happens</h3>
      <ol class="steps">
        <li>The compose files and <code>.env</code> are backed up for rollback.</li>
        <li>New images are pulled.</li>
        ${app.containers.some((c) => c.is_self)
          ? "<li>Home Assistant restarts on the new version; this page reconnects in about a minute.</li>"
          : "<li>Only changed containers are recreated.</li><li>All containers must become healthy within 5 minutes, otherwise the backup is restored automatically.</li>"}
      </ol>`;
    const dialog = this._dialog(
      `Update ${app.name}`,
      body,
      `<button class="btn" data-r="no">Cancel</button><button class="btn primary" data-r="yes">${icon("arrow-up")} Update</button>`,
    );
    markdown(dialog, "#release-notes", update.release_notes);
    return this._ask(dialog, () => true);
  }

  _pick(title, text, options) {
    const dialog = this._dialog(
      title,
      `<div class="muted">${esc(text)}</div><label>Version</label><select id="pick">${options.map((o) => `<option>${esc(o)}</option>`).join("")}</select>`,
      `<button class="btn" data-r="no">Cancel</button><button class="btn primary" data-r="yes">Roll back</button>`,
    );
    return this._ask(dialog, () => dialog.querySelector("#pick").value);
  }

  async _settings(app) {
    const dialog = this._dialog(
      `Update settings · ${app.name}`,
      `<label>Upstream compose URL (optional)</label>
       <input id="upstream" placeholder="https://github.com/org/app/releases/download/{version}/docker-compose.yml" value="${esc(app.upstream || "")}">
       <div class="muted" style="margin-top:6px">With <code>{version}</code>. Services the new version needs (for example a newer cache) are then updated with the app.
         A new database major version puts the update on hold.</div>
       <label class="toggle" style="margin-top:16px"><input type="checkbox" id="db-minor" ${app.allow_db_minor ? "checked" : ""}> Allow database minor updates</label>
       <label>Only tags matching (regular expression)</label><input id="include" class="mono" value="${esc(app.include || "")}">
       <label>Ignore tags matching (regular expression)</label><input id="exclude" class="mono" value="${esc(app.exclude || "")}">`,
      `<button class="btn" data-r="no">Cancel</button><button class="btn primary" data-r="yes">Save</button>`,
    );
    const values = await this._ask(dialog, () => ({
      upstream: dialog.querySelector("#upstream").value,
      allow_db_minor: dialog.querySelector("#db-minor").checked,
      include: dialog.querySelector("#include").value,
      exclude: dialog.querySelector("#exclude").value,
    }));
    if (values) await this._call("manage", { app: app.key, managed: true, ...values });
  }

  async _install(storeId) {
    const app = await this._ws("store/app", { app_id: storeId });
    const inputType = { password: "password", number: "number", email: "email", url: "url" };
    const fields = app.variables
      .filter((v) => v.type !== "random")
      .map((v) =>
        v.type === "boolean"
          ? `<label class="toggle"><input type="checkbox" data-var="${esc(v.key)}" ${String(v.default) === "true" ? "checked" : ""}> ${esc(v.label)}</label>`
          : `<label>${esc(v.label)}${v.required ? " *" : ""}</label>
             <input data-var="${esc(v.key)}" type="${inputType[v.type] || "text"}" value="${esc(v.default || "")}" ${v.required ? "required" : ""}>`,
      )
      .join("");
    const services = app.services.map((s) => `<li><b>${esc(s.name)}</b> <span class="muted mono">${esc(s.image)}</span></li>`).join("");
    const dialog = this._dialog(
      `Install ${app.name}`,
      `<div class="md" id="description"></div>
       <h3>Containers</h3><ul style="margin:0;padding-left:20px;line-height:1.8;font-size:14px">${services}</ul>
       <label>Name (compose project)</label><input id="app-name" value="${esc(app.name.toLowerCase().replace(/[^a-z0-9_-]+/g, "-"))}">
       ${app.web_port ? `<label>Web port</label><input data-var="APP_PORT" type="number" value="${esc(app.web_port)}">` : ""}
       ${fields}
       ${app.variables.some((v) => v.type === "random") ? `<div class="muted" style="margin-top:12px">Passwords and secrets are generated.</div>` : ""}
       <div class="muted" style="margin-top:12px">Files go to <code>${esc(this._state.apps_root)}/&lt;name&gt;</code>, data to <code>${esc(this._state.data_root)}/&lt;name&gt;</code>.</div>`,
      `<button class="btn" data-r="no">Cancel</button><button class="btn primary" data-r="install">Install</button>`,
    );
    markdown(dialog, "#description", app.description || app.tagline);
    dialog.oninput = (ev) => ev.target.classList.remove("invalid");
    dialog.onclick = async (ev) => {
      const r = ev.target.closest("[data-r]")?.dataset.r;
      if (r === "no" || r === "close") dialog.close();
      if (r !== "install") return;
      const missing = [...dialog.querySelectorAll("input[required]")].filter((i) => !i.value.trim());
      missing.forEach((i) => i.classList.add("invalid"));
      if (missing.length) {
        missing[0].focus();
        return;
      }
      const values = {};
      dialog.querySelectorAll("[data-var]").forEach((i) => {
        if (i.type === "checkbox") values[i.dataset.var] = String(i.checked);
        else if (i.value !== "") values[i.dataset.var] = i.value;
      });
      const button = ev.target.closest("button");
      const fields = [...dialog.querySelectorAll("input, select, button")];
      fields.forEach((field) => (field.disabled = true));
      button.textContent = "Installing…";
      try {
        await this._call("install", { store_app: storeId, name: dialog.querySelector("#app-name").value, values }, true);
        dialog.close();
        this._toast(`${app.name} installed`);
        this._setTab("apps");
      } catch (err) {
        fields.forEach((field) => (field.disabled = false));
        button.textContent = "Install";
      }
    };
  }

  _showLogs(title, keys) {
    const dialog = this._dialog(
      `Logs · ${title}`,
      `<div class="toolbar">
         ${keys.length > 1 ? `<select id="log-container">${keys.map((k) => `<option>${esc(k)}</option>`).join("")}</select>` : ""}
         <label class="toggle"><input type="checkbox" id="log-follow" checked> Follow</label></div>
       <pre class="logs" id="log-output">Loading…</pre>`,
      `<button class="btn" data-r="copy">${icon("content-copy")} Copy</button><button class="btn primary" data-r="close">Close</button>`,
      true,
    );
    const output = dialog.querySelector("#log-output");
    const start = async (key) => {
      this._stopLogs();
      output.textContent = "";
      let lines = 0;
      this._unsubLogs = await this._hass.connection.subscribeMessage(
        (msg) => {
          if (msg.lines) {
            // the first batch is the tail: always show its end
            const atBottom = lines === 0 || output.scrollHeight - output.scrollTop - output.clientHeight < 40;
            output.textContent += `${msg.lines.join("\n")}\n`;
            lines += msg.lines.length;
            if (lines > 5000) {
              output.textContent = output.textContent.split("\n").slice(-4000).join("\n");
              lines = 4000;
            }
            if (atBottom && dialog.querySelector("#log-follow").checked) output.scrollTop = output.scrollHeight;
          }
          if (msg.error) output.textContent += `\n[error] ${msg.error}\n`;
          if (msg.end) output.textContent += "\n[container stopped]\n";
        },
        { type: `${DOMAIN}/logs/subscribe`, container: key, tail: 300 },
      );
    };
    dialog.querySelector("#log-container")?.addEventListener("change", (ev) => start(ev.target.value));
    dialog.onclick = (ev) => {
      const r = ev.target.closest("[data-r]")?.dataset.r;
      if (r === "copy") navigator.clipboard?.writeText(output.textContent);
      if (r === "close") dialog.close();
    };
    dialog.onclose = () => this._stopLogs();
    start(keys[0]);
  }

  _stopLogs() {
    if (this._unsubLogs) {
      this._unsubLogs();
      this._unsubLogs = null;
    }
  }
}

// the page can load the module again after Home Assistant restarts
if (!customElements.get("docker-apps-panel")) customElements.define("docker-apps-panel", DockerAppsPanel);
