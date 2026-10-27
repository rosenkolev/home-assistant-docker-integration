// Docker Apps panel: apps, store, resources and live logs.
// Plain web component (no external libraries) so it works offline.
// cspell:words autorenew unsub

const DOMAIN = "docker_apps";
const REFRESH_MS = 5000;
const STORE_PAGE = 60;
const PORT_CHECK_MS = 400; // typing pause before the web port is checked again
const LOG_LINES_MAX = 5000; // above this the log view keeps only the newest lines
const LOG_LINES_KEEP = 4000;
// terminal color codes in logs; the log view shows plain text
const ANSI_CODES = /\x1b\[[0-9;?]*[A-Za-z]/g;
// tab -> URL hash, so a refresh opens the same tab
const TABS = { apps: ["installed", "Installed"], store: ["app-store", "App store"], resources: ["images-volumes", "Images &amp; volumes"] };
const tabFromHash = () => Object.keys(TABS).find((t) => `#${TABS[t][0]}` === location.hash) || "apps";

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
const webUrl = ([port, path, scheme]) => `${scheme || "http"}://${location.hostname}:${port}${path}`;

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

const iconButton = (action, name, title, { data = "", disabled = false, small = false } = {}) =>
  `<button class="icon-btn ${small ? "small" : ""}" title="${esc(title)}" aria-label="${esc(title)}" data-action="${action}" ${data} ${disabled ? "disabled" : ""}>${icon(name)}</button>`;

const tile = (value, label, accent = false) =>
  `<div class="tile ${accent ? "accent" : ""}"><div class="value">${value}</div><div class="label">${label}</div></div>`;

const reclaimable = (disk) => (disk?.images_reclaimable || 0) + (disk?.volumes_reclaimable || 0);

// dialog footer: Cancel and one action button; `data-r` names the action
const footer = (label, r = "yes", style = "primary", disabled = false) =>
  `<button class="btn" data-r="no">Cancel</button><button class="btn ${style}" data-r="${r}" ${disabled ? "disabled" : ""}>${label}</button>`;

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
  .chip.ok { background: rgba(var(--rgb-success-color, 67,160,71), .14); color: var(--success-color, #43a047); }
  .chip.warn, .chip.busy { background: rgba(var(--rgb-warning-color, 255,152,0), .16); color: var(--warning-color, #ef8a00); }
  .chip.bad { background: rgba(var(--rgb-error-color, 219,68,55), .14); color: var(--error-color, #db4437); }
  .chip.off { background: var(--secondary-background-color); color: var(--secondary-text-color); }
  .chip.info { background: rgba(var(--rgb-primary-color, 3,169,244), .12); color: var(--primary-color); }
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
    background: rgba(var(--rgb-primary-color, 3,169,244), .10); }
  .banner ha-icon { color: var(--primary-color); }
  .banner.hold { background: rgba(var(--rgb-warning-color, 255,152,0), .12); }
  .banner.hold ha-icon { color: var(--warning-color, #ef8a00); }
  .banner.error { background: rgba(var(--rgb-error-color, 219,68,55), .10); }
  .banner .title { font-weight: 500; font-size: 14px; }
  .text { font-size: 14px; line-height: 1.5; }
  .hint { margin-top: 12px; }
  ul.list { margin: 0; padding-left: 20px; line-height: 1.8; font-size: 14px; }

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
    font: inherit; font-size: 14px; color: inherit; cursor: pointer; text-align: left; white-space: nowrap; }
  .menu button:hover:not(:disabled) { background: var(--secondary-background-color); }
  .menu button ha-icon { --mdc-icon-size: 20px; color: var(--secondary-text-color); }
  .menu button.danger, .menu button.danger ha-icon { color: var(--error-color, #db4437); }
  progress { width: 100%; height: 4px; margin-top: 12px; accent-color: var(--primary-color); }

  .card + .card { margin-top: 16px; }
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
  dialog .body li { overflow-wrap: anywhere; }
  .mount { display: grid; grid-template-columns: auto minmax(0, 1fr) auto auto; gap: 4px 10px; align-items: center;
    padding: 10px 0; border-top: 1px solid var(--divider-color); }
  .mount ha-icon { color: var(--secondary-text-color); }
  .mount .paths { min-width: 0; font-size: 13px; }
  .mount .paths .mono { overflow-wrap: anywhere; }
  .mount label.check { display: inline-flex; align-items: center; gap: 6px; margin: 0; font-size: 13px; color: inherit; white-space: nowrap; }
  .mount label.check input, .add label.check input { width: auto; }
  .mount label.check:has(input:disabled) { color: var(--secondary-text-color); }
  .add { display: grid; gap: 8px; margin-top: 4px; }
  .add label.check { display: inline-flex; align-items: center; gap: 6px; margin: 4px 0 0; color: inherit; }
  .combo { position: relative; }
  /* inline, not floating: the dialog body scrolls and would cut a floating list */
  .suggest { margin-top: 4px; max-height: 240px; overflow: auto;
    background: var(--card-background-color); border: 1px solid var(--divider-color); border-radius: 10px;
    box-shadow: 0 6px 24px rgba(0,0,0,.18); padding: 4px 0; }
  .suggest .group { padding: 8px 12px 4px; font-size: 11px; font-weight: 500; text-transform: uppercase; letter-spacing: .04em;
    color: var(--secondary-text-color); }
  .suggest button { display: block; width: 100%; text-align: left; border: none; background: none; padding: 6px 12px;
    font: inherit; color: inherit; cursor: pointer; }
  .suggest button:hover { background: var(--secondary-background-color); }
  .suggest button .muted { display: block; font-size: 12px; }
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
    background: rgba(var(--rgb-warning-color, 255,152,0), .12); }
  .empty { padding: 48px 16px; text-align: center; color: var(--secondary-text-color); }
  .empty.small { padding: 16px; }
  .grid > .empty { grid-column: 1 / -1; }
  a { color: var(--primary-color); }
  @media (max-width: 600px) { main { padding: 12px; } .grid { grid-template-columns: 1fr; } header .host { display: none; } }
`;

class DockerAppsPanel extends HTMLElement {
  constructor() {
    super();
    this.attachShadow({ mode: "open" });
    this._tab = tabFromHash();
    this._filter = "";
    this._category = "";
    this._state = null;
    this._store = null;
    this._resources = null;
    this._expandedApps = new Set();
    this._menu = null;
    this._error = null;
    this._shown = STORE_PAGE;
    this._loadSeq = 0; // the newest _load wins; older answers are dropped
    this._pending = new Set(); // keys of actions this page runs (app, container, ...)
    this._logGen = 0; // the newest log subscription wins
  }

  set hass(hass) {
    const first = !this._hass;
    this._hass = hass;
    if (first) {
      this._renderShell();
      this._load();
    } else {
      this.shadowRoot.querySelector("ha-menu-button").hass = hass;
    }
  }

  set narrow(value) {
    this._narrow = value;
    const menu = this.shadowRoot.querySelector("ha-menu-button");
    if (menu) menu.narrow = value;
  }

  connectedCallback() {
    this._timer = setInterval(() => {
      // the store refreshes only while an app is being installed
      const live = this._tab === "apps" || (this._tab === "store" && this._store?.apps.some((a) => a.installing));
      if (document.visibilityState === "visible" && live && !this._dialogOpen() && !this._menu) this._load();
    }, REFRESH_MS);
    // on the document: a re-render moves the focus out of the panel
    this._onKey = (ev) => {
      if (ev.key === "Escape" && this._menu) this._closeMenu();
    };
    document.addEventListener("keydown", this._onKey);
  }

  disconnectedCallback() {
    clearInterval(this._timer);
    document.removeEventListener("keydown", this._onKey);
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
      const result = await this._hass.callService(DOMAIN, service, data, undefined, false, response);
      return response ? result?.response : result;
    } catch (err) {
      this._toast(err.message || String(err));
      err.notified = true;
      throw err;
    }
  }

  _toast(message) {
    this.dispatchEvent(new CustomEvent("hass-notification", { detail: { message }, bubbles: true, composed: true }));
  }

  _showError(err) {
    if (!err.notified) this._toast(err.message || String(err));
  }

  async _load() {
    const tab = this._tab;
    const seq = ++this._loadSeq;
    const extraView = { store: "store", resources: "resources" }[tab];
    try {
      const needState = tab !== "store" || !this._state;
      const [state, extra] = await Promise.all([
        needState ? this._ws("state") : this._state,
        extraView ? this._ws(extraView) : null,
      ]);
      if (seq !== this._loadSeq) return;
      this._state = state;
      if (state.now) clockOffset = new Date(state.now).getTime() - Date.now();
      if (tab === "store") this._store = extra;
      if (tab === "resources") this._resources = extra;
      this._error = null;
    } catch (err) {
      if (seq !== this._loadSeq) return;
      this._error = err.message || String(err);
    }
    this._renderMain();
  }

  _renderShell() {
    this.shadowRoot.innerHTML = `
      <style>${STYLE}</style>
      <header><ha-menu-button></ha-menu-button><h1>Apps</h1><span class="host"></span></header>
      <nav>${Object.entries(TABS)
        .map(([tab, [, label]]) => `<button data-tab="${tab}" class="${tab === this._tab ? "active" : ""}">${label}</button>`)
        .join("")}</nav>
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
    main.addEventListener("change", (ev) => this._onChange(ev));
    // the app menu closes on a click anywhere else (and on Escape, see connectedCallback)
    this.shadowRoot.addEventListener("click", (ev) => {
      if (this._menu && !ev.target.closest(".menu, [data-action=menu]")) this._closeMenu();
    });
  }

  _closeMenu() {
    this._menu = null;
    this._renderMain();
  }

  _setTab(tab) {
    this._tab = tab;
    history.replaceState(history.state, "", `#${TABS[tab][0]}`);
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
    const updates = state.apps.filter((a) => this._hasUpdate(a)).length;
    const managed = state.apps.filter((a) => a.managed).length;
    return `
      ${notices.map((n) => `<div class="notice">${icon("information-outline")}<div>${n}</div></div>`).join("")}
      <div class="tiles">
        ${tile(state.apps.length, "Apps")}
        ${tile(`${state.host.running}<span class="muted"> / ${state.host.containers}</span>`, "Containers running")}
        ${tile(managed, "Managed")}
        ${tile(updates, "Updates", updates > 0)}
        ${tile(bytes(reclaimable(state.disk)), "Reclaimable")}
      </div>
      <div class="toolbar">
        <input id="filter" placeholder="Search apps" value="${esc(this._filter)}" data-input="filter">
        <button class="btn" data-action="check-updates" ${this._pending.has("check-updates") ? "disabled" : ""}>${icon("refresh")} Check for updates</button>
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
    let iconName = "docker";
    if (app.compose) iconName = "layers-outline";
    if (isSelf) iconName = "home-assistant";
    const appIcon = app.icon ? `<img class="app-icon" src="${esc(app.icon)}" alt="">` : `<div class="app-icon">${icon(iconName)}</div>`;
    const busy = this._isBusy(app);

    let banner = "";
    if (update?.blocked) {
      banner = `<div class="banner hold">${icon("pause-circle-outline")}<div class="grow"><div class="title">Update on hold</div>
        <div class="muted">${esc(update.blocked)}</div></div></div>`;
    } else if (!app.busy && this._hasUpdate(app)) {
      // while the app changes, a check can see it half updated: no banner then
      banner = `<div class="banner">${icon("arrow-up-circle-outline")}<div class="grow">
          <div class="title">Update available: ${esc(update.latest)}</div>
          <div class="muted">${update.store ? "New app package" : `${update.changes.length} service${update.changes.length === 1 ? "" : "s"} change`}</div></div>
        ${readOnly ? "" : `<button class="btn" data-action="skip" ${data} ${busy ? "disabled" : ""} title="Hide this version until a newer one comes">Skip</button>
          <button class="btn primary" data-action="update" ${data} ${busy ? "disabled" : ""}>Update</button>`}</div>`;
    } else if (update?.error) {
      banner = `<div class="banner error">${icon("alert-circle-outline")}<div class="grow muted">Update check failed: ${esc(update.error)}</div></div>`;
    }

    const menuItems = [
      ["launcher", app.launcher ? "checkbox-marked-outline" : "checkbox-blank-outline", "Show in app launcher"],
      app.configurable ? ["environment", "cog-outline", "Settings & passwords…"] : null,
      app.configurable ? ["volumes", "folder-multiple-outline", "Folders & volumes…"] : null,
      app.compose && app.editable ? ["recreate", "autorenew", "Recreate"] : null,
      app.history?.length && app.editable ? ["rollback", "history", "Roll back…"] : null,
      app.managed && !app.store ? ["settings", "tune-variant", "Update settings…"] : null,
      isSelf ? null : ["uninstall", "delete-outline", "Remove…", "danger"],
    ].filter(Boolean);
    const button = (action, name, title) => iconButton(action, name, title, { data, disabled: busy });
    const power = cls === "off" ? button("start", "play", "Start") : button("stop", "stop", "Stop");
    const actions = `
      <div class="section footer">
        ${readOnly || !app.compose ? `<span class="grow"></span>` : `<label class="toggle grow" title="Check for updates and allow one-click updates">
          <input type="checkbox" data-input="managed" ${data} ${app.managed ? "checked" : ""} ${app.editable || app.store ? "" : "disabled"}> Managed</label>`}
        ${app.web ? `<a class="icon-btn" href="${esc(webUrl(app.web))}" target="_blank" rel="noopener" title="Open web UI" aria-label="Open web UI">${icon("open-in-new")}</a>` : ""}
        ${iconButton("logs", "text-box-outline", "Logs", { data })}
        ${readOnly ? "" : button("restart", "restart", "Restart")}
        ${readOnly || isSelf ? "" : power}
        ${readOnly || !menuItems.length ? "" : `<div class="menu-wrap">
          ${iconButton("menu", "dots-vertical", "More", { data, disabled: busy })}
          ${this._menu === app.key ? `<div class="menu">${menuItems
            .map(([action, name, text, style]) => `<button class="${style || ""}" data-action="${action}" ${data}>${icon(name)}${text}</button>`)
            .join("")}</div>` : ""}
        </div>`}
      </div>`;

    const open = this._expandedApps.has(app.key);
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
          ${open ? app.containers.map((c) => this._renderContainer(app, c, readOnly, busy)).join("") : ""}
        </div>
        ${actions}
      </div>`;
  }

  _renderContainer(app, c, readOnly, busy) {
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
    const serviceData = `data-app="${esc(app.key)}" data-service="${esc(c.service)}" data-tag="${esc(c.latest)}"`;
    const updateOne = c.latest && !c.tracked && !readOnly && app.editable
      ? iconButton("skip-service", "skip-next-outline", `Skip ${c.latest} of ${c.service}`, { data: serviceData, disabled: busy, small: true }) +
        iconButton("update-service", "arrow-up-bold-circle-outline", `Update ${c.service} to ${c.latest}`, { data: serviceData, disabled: busy, small: true })
      : "";
    const containerData = `data-container="${esc(c.key)}"`;
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
          ${iconButton("container-logs", "text-box-outline", "Logs", { data: containerData, small: true })}
          ${readOnly || c.is_self ? "" : iconButton("container-restart", "restart", "Restart", { data: containerData, disabled: busy, small: true })}
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
        <button class="btn" data-action="refresh-store" ${this._pending.has("refresh-store") ? "disabled" : ""}>${icon("refresh")} Refresh</button>
      </div>
      <div class="grid">${apps.slice(0, this._shown).map((a) => this._renderStoreApp(a)).join("") || `<div class="empty">No apps found</div>`}</div>
      ${apps.length > this._shown ? `<div class="empty"><button class="btn" data-action="more">Show more (${apps.length - this._shown} left)</button></div>` : ""}`;
  }

  _renderStoreApp(app) {
    const repo = this._store.repositories.find((r) => r.id === app.repo_id);
    const appIcon = app.icon ? `<img class="app-icon" loading="lazy" src="${esc(app.icon)}" alt="">` : `<div class="app-icon">${icon("package-variant")}</div>`;
    let action = `<button class="btn primary" data-action="install" data-store="${esc(app.id)}">Install</button>`;
    if (app.installed) action = `<span class="chip ok">${icon("check")}Installed</span>`;
    else if (app.installing) action = `<span class="chip busy">${icon("progress-download")}Installing…</span>`;
    else if (!app.supported) action = `<span class="chip off">Not for ${esc(this._state?.host?.arch || "this CPU")}</span>`;
    else if (this._state?.read_only) action = "";
    return `
      <div class="card"><div class="section">
        <div class="row">${appIcon}
          <div class="grow"><div class="name">${esc(app.name)}</div>
          <div class="muted ellipsis">${esc(app.version)} · ${esc(repo?.name || "")}</div></div>
          ${action}
        </div>
        <div class="muted hint">${esc(app.tagline)}</div>
      </div></div>`;
  }

  // --- resources ---

  _renderResources() {
    const res = this._resources;
    const disk = this._state?.disk;
    if (!res) return `<div class="empty">Loading…</div>`;
    const readOnly = this._state?.read_only;
    // one prune at a time: Docker refuses a second one and blocks listing while it runs
    const prune = (kind, text) =>
      readOnly
        ? ""
        : `<button class="btn" data-action="prune" data-kind="${kind}" ${this._pruning ? "disabled" : ""}>${this._pruning === kind ? "Removing…" : text}</button>`;
    const table = (title, headers, rows) => `
      <div class="card"><div class="section"><div class="name">${title}</div></div>
        <div class="scroll"><table><tr>${headers.map((h) => `<th>${h}</th>`).join("")}</tr>${rows}</table></div></div>`;
    return `
      <div class="tiles">
        ${tile(bytes(disk?.images_size), `Images · ${disk?.images_unused ?? "–"} unused`)}
        ${tile(bytes(disk?.volumes_size), `Volumes · ${disk?.volumes_unused ?? "–"} unused`)}
        ${tile(bytes(disk?.build_cache_size), "Build cache")}
        ${tile(bytes(reclaimable(disk)), "Reclaimable", true)}
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
    if (ev.target.dataset.input !== "filter") return;
    this._filter = ev.target.value;
    this._shown = STORE_PAGE;
    this._renderMain();
  }

  _onChange(ev) {
    const { input, app } = ev.target.dataset;
    if (input === "category") {
      this._category = ev.target.value;
      this._shown = STORE_PAGE;
      this._renderMain();
    } else if (input === "managed") {
      const managed = ev.target.checked;
      this._call("manage", { app, managed })
        .then(() => this._toast(managed ? "Managed: checking for updates…" : "Not managed anymore"))
        .catch(() => {})
        .finally(() => this._load());
    }
  }

  async _onClick(ev) {
    const button = ev.target.closest("[data-action]");
    if (!button || button.disabled) return;
    const { action, app, container, kind, store, service, tag } = button.dataset;
    const appInfo = this._state?.apps.find((a) => a.key === app);
    if (action === "toggle") {
      if (!this._expandedApps.delete(app)) this._expandedApps.add(app);
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
    if (this._menu) this._closeMenu();
    // one action per app (container, store app, ...) at a time; its buttons are
    // disabled until it ends, other apps stay usable
    const key = app || container || store || action;
    if (this._pending.has(key)) return;
    this._pending.add(key);
    this._renderMain();
    try {
      switch (action) {
        case "check-updates":
          await this._call("check_updates", { repositories: false });
          break;
        case "refresh-store":
          await this._call("check_updates", { repositories: true });
          break;
        case "update":
          if (await this._confirmUpdate(appInfo)) await this._callEntity("update", "install", appInfo);
          break;
        case "skip":
          await this._callEntity("update", "skip", appInfo);
          this._toast(`${appInfo.name} ${appInfo.update.latest} skipped`);
          break;
        case "skip-service":
          await this._call("skip_update", { app, services: [service] });
          this._toast(`${service} ${tag} skipped`);
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
          const dialog = this._dialog(
            `Remove ${appInfo.name}?`,
            `<div class="text">The containers and networks of the app are removed.</div>
             ${appInfo.store
               ? `<label class="toggle"><input type="checkbox" id="remove-data"> Also delete the app folder and its data</label>`
               : `<div class="muted hint">The compose files and volumes stay, so it can be started again with <code>docker compose up -d</code>.</div>`}`,
            footer("Remove", "yes", "primary danger"),
          );
          const answer = await this._ask(dialog, () => ({ remove_data: Boolean(dialog.querySelector("#remove-data")?.checked) }));
          if (answer) await this._call("uninstall", { app, ...answer });
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
        case "environment":
          await this._environment(appInfo);
          break;
        case "volumes":
          await this._volumes(appInfo);
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
          if (await this._confirm("Remove unused resources?", `${esc(button.textContent)}. This can't be undone.`, "Remove", "primary danger")) {
            this._pruning = kind;
            this._renderMain();
            try {
              const result = await this._call("prune", { kind }, true);
              this._toast(`Removed ${result?.removed ?? 0} items, freed ${bytes(result?.space_reclaimed)}`);
            } finally {
              this._pruning = null;
            }
          }
          break;
        case "install":
          await this._install(store);
          break;
        default:
          break;
      }
    } catch (err) {
      this._showError(err);
    } finally {
      this._pending.delete(key);
    }
    this._load();
  }

  // busy on the server (any browser), or an action of this page still runs; Home
  // Assistant's own app changes alone, as its update restarts Home Assistant
  _isBusy(app) {
    const changing = (a) => Boolean(a.busy) || this._pending.has(a.key);
    if (changing(app)) return true;
    const isSelf = (a) => a.containers.some((c) => c.is_self);
    const others = this._state.apps.filter((a) => a !== app && changing(a));
    return isSelf(app) ? others.length > 0 : others.some(isSelf);
  }

  // an update that is not skipped (skipping is kept by the update entity, as in Settings > Updates)
  _hasUpdate(app) {
    if (!app.update?.available) return false;
    const entity = this._entity(app, "update");
    const skipped = entity && this._hass.states[entity.entity_id]?.attributes.skipped_version;
    return !skipped || skipped !== app.update.latest;
  }

  _entity(app, domain) {
    return Object.values(this._hass.entities).find((e) => e.device_id === app.device_id && e.entity_id.startsWith(`${domain}.`));
  }

  async _callEntity(domain, service, app, extra = {}) {
    const entity = this._entity(app, domain);
    if (!entity) throw new Error(`${domain} entity of ${app.name} not found`);
    await this._hass.callService(domain, service, { entity_id: entity.entity_id, ...extra }, undefined, false);
  }

  // --- dialogs ---

  // One dialog element is reused. Buttons carry `data-r`: close and no close the
  // dialog, every other value goes to this._dialogAction(r, button); closing calls
  // this._dialogClosed().
  _dialog(title, body, buttons, wide = false) {
    const dialog = this.shadowRoot.getElementById("dialog");
    dialog.oninput = dialog.onchange = null;
    this._dialogAction = this._dialogClosed = null;
    // the close event comes a moment after close(): when the next dialog is open
    // by then, it belongs to the previous one
    dialog.onclose = () => {
      if (!dialog.open) this._dialogClosed?.();
    };
    dialog.onclick = (ev) => {
      const button = ev.target.closest("[data-r]");
      const r = button?.dataset.r;
      if (!r) return;
      if (r === "close" || r === "no") dialog.close();
      else this._dialogAction?.(r, button);
    };
    dialog.className = wide ? "wide" : "";
    dialog.innerHTML = `<div class="head"><div class="name grow">${esc(title)}</div>
        <button class="icon-btn" data-r="close" aria-label="Close">${icon("close")}</button></div>
      <div class="body">${body}</div>
      <div class="foot">${buttons}</div>`;
    dialog.showModal();
    return dialog;
  }

  // resolves with onYes() on the yes button, null when the dialog is closed otherwise
  _ask(dialog, onYes) {
    return new Promise((resolve) => {
      this._dialogAction = (r) => {
        if (r !== "yes") return;
        resolve(onYes());
        dialog.close();
      };
      this._dialogClosed = () => resolve(null);
    });
  }

  // runs work with the dialog fields locked; the dialog closes on success and
  // stays open (fields unlocked) when the work fails
  async _save(dialog, button, label, work) {
    // fields disabled before stay disabled afterwards
    const fields = [...dialog.querySelectorAll("input, select, button")].filter((field) => !field.disabled);
    const text = button.textContent;
    fields.forEach((field) => (field.disabled = true));
    button.textContent = label;
    try {
      await work();
      dialog.close();
      return true;
    } catch (err) {
      this._showError(err);
      fields.forEach((field) => (field.disabled = false));
      button.textContent = text;
      return false;
    }
  }

  _confirm(title, body, label, style = "primary") {
    const dialog = this._dialog(title, `<div class="text">${body}</div>`, footer(esc(label), "yes", style));
    return this._ask(dialog, () => true);
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
      ${kept ? `<h3>Stays as it is</h3><ul class="list">${kept}</ul>` : ""}
      <h3>What happens</h3>
      <ol class="steps">
        <li>The compose files and <code>.env</code> are backed up for rollback.</li>
        <li>New images are pulled.</li>
        ${app.containers.some((c) => c.is_self)
          ? "<li>Home Assistant restarts on the new version; this page reconnects in about a minute.</li>"
          : "<li>Only changed containers are recreated.</li><li>All containers must become healthy within 5 minutes, otherwise the backup is restored automatically.</li>"}
      </ol>`;
    const dialog = this._dialog(`Update ${app.name}`, body, footer(`${icon("arrow-up")} Update`));
    markdown(dialog, "#release-notes", update.release_notes);
    return this._ask(dialog, () => true);
  }

  _pick(title, text, options) {
    const dialog = this._dialog(
      title,
      `<div class="muted">${esc(text)}</div><label>Version</label><select id="pick">${options.map((o) => `<option>${esc(o)}</option>`).join("")}</select>`,
      footer("Roll back"),
    );
    return this._ask(dialog, () => dialog.querySelector("#pick").value);
  }

  async _settings(app) {
    const dialog = this._dialog(
      `Update settings · ${app.name}`,
      `<label>Upstream compose URL (optional)</label>
       <input id="upstream" placeholder="https://github.com/org/app/releases/download/{version}/docker-compose.yml" value="${esc(app.upstream || "")}">
       <div class="muted hint">With <code>{version}</code>. Services the new version needs (for example a newer cache) are then updated with the app.
         A new database major version puts the update on hold.</div>
       <label class="toggle"><input type="checkbox" id="db-minor" ${app.allow_db_minor ? "checked" : ""}> Allow database minor updates</label>
       <label>Only tags matching (regular expression)</label><input id="include" class="mono" value="${esc(app.include || "")}">
       <label>Ignore tags matching (regular expression)</label><input id="exclude" class="mono" value="${esc(app.exclude || "")}">`,
      footer("Save"),
    );
    const values = await this._ask(dialog, () => ({
      upstream: dialog.querySelector("#upstream").value,
      allow_db_minor: dialog.querySelector("#db-minor").checked,
      include: dialog.querySelector("#include").value,
      exclude: dialog.querySelector("#exclude").value,
    }));
    if (values) await this._call("manage", { app: app.key, managed: true, ...values });
  }

  async _environment(app) {
    const { services } = await this._ws("app/environment", { app: app.key });
    const secret = /pass|secret|token|key/i;
    const sections = Object.entries(services)
      .filter(([, env]) => Object.keys(env).length)
      .map(
        ([service, env]) => `<h3>${esc(service)}</h3>${Object.entries(env)
          .map(
            ([key, value]) => `<label>${esc(key)}</label>
              <div class="row"><input class="mono grow" data-service="${esc(service)}" data-key="${esc(key)}"
                type="${secret.test(key) ? "password" : "text"}" value="${esc(value ?? "")}">
              ${secret.test(key) ? `<button class="icon-btn small" data-r="reveal" title="Show" aria-label="Show">${icon("eye-outline")}</button>` : ""}</div>`,
          )
          .join("")}`,
      )
      .join("");
    const dialog = this._dialog(
      `Settings · ${app.name}`,
      `<div class="muted">Changes are kept in <code>compose.override.yaml</code>, so they stay after updates. Saving recreates the changed containers.</div>
       ${sections || `<div class="empty">This app has no environment variables.</div>`}`,
      footer("Save", "save", "primary", !sections),
    );
    this._dialogAction = async (r, button) => {
      if (r === "reveal") {
        const input = button.parentElement.querySelector("input");
        input.type = input.type === "password" ? "text" : "password";
        return;
      }
      if (r !== "save") return;
      const changes = {};
      dialog.querySelectorAll("input[data-key]").forEach((input) => {
        if (input.value !== input.defaultValue) {
          (changes[input.dataset.service] ??= {})[input.dataset.key] = input.value;
        }
      });
      if (!Object.keys(changes).length) {
        dialog.close();
        return;
      }
      const saved = await this._save(dialog, button, "Saving…", async () => {
        for (const [service, variables] of Object.entries(changes)) {
          await this._call("set_environment", { app: app.key, service, variables });
        }
      });
      if (saved) this._toast(`${app.name} settings saved`);
      this._load();
    };
  }

  async _volumes(app) {
    const info = await this._ws("app/volumes", { app: app.key });
    const names = Object.keys(info.services);
    const rows = Object.fromEntries(names.map((name) => [name, info.services[name].map((m) => ({ ...m }))]));
    const original = JSON.stringify(rows);
    const haConfig = info.suggestions.home_assistant.find((m) => m.target === "/config")?.source;
    let service = names[0];
    // grouped suggestions for the "Folder or volume" field
    const groups = [
      ["Home Assistant", info.suggestions.home_assistant.map((m) => ({ source: m.source, note: `Home Assistant ${m.target}` }))],
      ["Docker volumes", info.suggestions.volumes.map((v) => ({ source: v.source, note: v.app ? `volume of ${v.app}` : "volume" }))],
      ...Object.entries(info.suggestions.apps)
        .filter(([key]) => key !== app.key)
        .map(([key, mounts]) => [`Used by ${key}`, mounts.map((m) => ({ source: m.source, note: `as ${m.target}` }))]),
    ].filter(([, items]) => items.length);
    const mountIcon = (m) => {
      if (!m.source) return "help-circle-outline";
      return m.source.startsWith("/") ? "folder-outline" : "database-outline";
    };

    const dialog = this._dialog(
      `Folders & volumes · ${app.name}`,
      `<div class="muted">Folders on the NAS and Docker volumes the app can use. Changes are kept in <code>compose.override.yaml</code>, so they stay after updates. Saving recreates the app.</div>
       ${names.length > 1 ? `<label>Container</label><select id="service">${names.map((n) => `<option>${esc(n)}</option>`).join("")}</select>` : ""}
       <div id="mounts"></div>
       <div id="mount-notes"></div>
       <h3>Add a folder or volume</h3>
       <div class="add">
         <div class="combo"><input id="source" class="mono" placeholder="Folder on the NAS or Docker volume, e.g. /mnt/nas/media" autocomplete="off"><div class="suggest" hidden></div></div>
         <input id="target" class="mono" placeholder="Path in the app, e.g. /media">
         <div class="row"><label class="check grow"><input type="checkbox" id="read-only"> Read-only</label>
           <button class="btn" data-r="add">${icon("plus")} Add</button></div>
       </div>`,
      footer("Save", "save"),
      true,
    );
    const $ = (selector) => dialog.querySelector(selector);
    const suggest = $(".suggest");

    const render = () => {
      const list = rows[service];
      $("#mounts").innerHTML = list.length
        ? list
            .map(
              (m, index) => `<div class="mount">
                ${icon(mountIcon(m))}
                <div class="paths"><div class="mono">${esc(m.source || "(anonymous volume)")}</div>
                  <div class="muted">in the app: <span class="mono">${esc(m.target)}</span>${m.yours === false ? ` · <span title="Comes with the app">from app</span>` : ""}</div></div>
                <label class="check" ${m.added ? "" : `title="Remove and add the folder again to change this"`}><input type="checkbox" data-index="${index}"
                  ${m.read_only ? "checked" : ""} ${m.added ? "" : "disabled"}> Read-only</label>
                ${m.yours === false
                  ? `<span class="icon-btn small" title="Comes with the app, so it stays">${icon("lock-outline")}</span>`
                  : `<button class="icon-btn small" data-r="remove" data-index="${index}" title="Remove" aria-label="Remove">${icon("delete-outline")}</button>`}
              </div>`,
            )
            .join("")
        : `<div class="empty small">No folders or volumes.</div>`;
      const seesConfig = haConfig && list.some((m) => m.source && (m.source === haConfig || m.source.startsWith(`${haConfig}/`)));
      $("#mount-notes").innerHTML = seesConfig
        ? `<div class="banner hold">${icon("alert-outline")}<div class="grow text">The app can read and change your Home Assistant configuration, including <code>secrets.yaml</code>.</div></div>`
        : "";
    };

    const defaultTarget = (source) => {
      const name = source.replace(/\/+$/, "").split("/").pop() || "data";
      const base = info.workspaces[service] || "/mnt";
      return `${base.replace(/\/+$/, "")}/${name}`;
    };

    const showSuggestions = () => {
      const query = $("#source").value.toLowerCase();
      const html = groups
        .map(([title, items]) => {
          const matches = items.filter((i) => !query || i.source.toLowerCase().includes(query) || i.note.toLowerCase().includes(query));
          return matches.length
            ? `<div class="group">${esc(title)}</div>${matches
                .map((i) => `<button type="button" data-pick="${esc(i.source)}"><span class="mono">${esc(i.source)}</span><span class="muted">${esc(i.note)}</span></button>`)
                .join("")}`
            : "";
        })
        .join("");
      suggest.innerHTML = html;
      suggest.hidden = !html;
      if (html) suggest.scrollIntoView({ block: "nearest" });
    };

    const addProblem = (source, target) => {
      if (!source) return "Choose a folder or a volume.";
      if (!target.startsWith("/") || target === "/") return "The path in the app must start with / and can't be / itself.";
      if (rows[service].some((m) => m.target === target)) return `${target} is already used in this app.`;
      return "";
    };

    $("#source").addEventListener("focus", showSuggestions);
    $("#source").addEventListener("input", showSuggestions);
    $("#source").addEventListener("blur", () => (suggest.hidden = true));
    $("#source").addEventListener("keydown", (ev) => {
      if (ev.key === "Escape" && !suggest.hidden) {
        ev.preventDefault();
        ev.stopPropagation();
        suggest.hidden = true;
      }
    });
    // mousedown keeps the focus in the field while a suggestion is picked
    suggest.addEventListener("mousedown", (ev) => {
      const pick = ev.target.closest("[data-pick]");
      if (!pick) return;
      ev.preventDefault();
      $("#source").value = pick.dataset.pick;
      if (!$("#target").value) $("#target").value = defaultTarget(pick.dataset.pick);
      suggest.hidden = true;
    });
    dialog.onchange = (ev) => {
      const { id, dataset } = ev.target;
      if (id === "service") {
        service = ev.target.value;
        render();
      } else if (dataset.index !== undefined) {
        rows[service][Number(dataset.index)].read_only = ev.target.checked;
      }
    };

    render();
    this._dialogAction = async (r, button) => {
      if (r === "remove") {
        rows[service].splice(Number(button.dataset.index), 1);
        render();
      }
      if (r === "add") {
        const source = $("#source").value.trim();
        const target = $("#target").value.trim() || (source ? defaultTarget(source) : "");
        const problem = addProblem(source, target);
        if (problem) {
          this._toast(problem);
          return;
        }
        rows[service].push({ source, target, read_only: $("#read-only").checked, yours: true, added: true });
        $("#source").value = "";
        $("#target").value = "";
        $("#read-only").checked = false;
        render();
      }
      if (r !== "save") return;
      const before = JSON.parse(original);
      const changed = names.filter((name) => JSON.stringify(rows[name]) !== JSON.stringify(before[name]));
      if (!changed.length) {
        dialog.close();
        return;
      }
      const saved = await this._save(dialog, button, "Saving…", async () => {
        for (const name of changed) {
          const volumes = rows[name].map(({ source, target, read_only }) => ({ source, target, read_only }));
          await this._call("set_volumes", { app: app.key, service: name, volumes });
        }
      });
      if (saved) this._toast(`${app.name} folders saved`);
      this._load();
    };
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
       <h3>Containers</h3><ul class="list">${services}</ul>
       <label>Name (compose project)</label><input id="app-name" value="${esc(app.name.toLowerCase().replace(/[^a-z0-9_-]+/g, "-"))}">
       ${app.web_port ? `<label>Web port</label><input data-var="APP_PORT" type="number" value="${esc(app.ports.web_port || app.web_port)}">` : ""}
       <div id="ports"></div>
       ${fields}
       ${app.variables.some((v) => v.type === "random") ? `<div class="muted hint">Passwords and secrets are generated.</div>` : ""}
       <div class="muted hint">Files go to <code>${esc(this._state.apps_root)}/&lt;name&gt;</code>, data to <code>${esc(this._state.data_root)}/&lt;name&gt;</code>.</div>`,
      footer("Install", "install"),
    );
    markdown(dialog, "#description", app.description || app.tagline);
    const installButton = dialog.querySelector("[data-r=install]");
    const showPorts = (ports, first = false) => {
      const notes = [];
      let blocked = false;
      if (ports.web_port_taken && ports.web_port !== ports.requested_web_port) {
        notes.push(
          first
            ? ["info", `Port ${ports.requested_web_port} is taken, so the free port ${ports.web_port} is filled in.`]
            : ["bad", `Port ${ports.requested_web_port} is taken. <button class="chip info" data-r="use-port" data-port="${ports.web_port}">Use ${ports.web_port}</button>`],
        );
        blocked = !first;
      } else if (ports.web_port_taken) {
        notes.push(["bad", `Port ${ports.requested_web_port} is taken. Choose another one.`]);
        blocked = true;
      }
      if (ports.busy.length) {
        notes.push(["bad", `Port ${ports.busy.join(", ")} is used by another app or service, and ${esc(app.name)} needs it, so it can't be installed.`]);
        blocked = true;
      }
      dialog.querySelector("#ports").innerHTML = notes
        .map(([cls, text]) => `<div class="banner ${cls === "bad" ? "error" : ""}">${icon(cls === "bad" ? "alert-circle-outline" : "information-outline")}<div class="grow text">${text}</div></div>`)
        .join("");
      installButton.disabled = blocked;
    };
    showPorts(app.ports, true);
    let portTimer;
    let portCheck = 0; // only the newest port check may change the notes
    dialog.oninput = (ev) => {
      ev.target.classList.remove("invalid");
      if (ev.target.dataset.var !== "APP_PORT") return;
      clearTimeout(portTimer);
      const port = Number(ev.target.value);
      if (!port) return;
      portTimer = setTimeout(async () => {
        const check = ++portCheck;
        try {
          const ports = await this._ws("store/ports", { app_id: storeId, web_port: port });
          if (check === portCheck && dialog.open) showPorts(ports);
        } catch (err) {
          this._showError(err);
        }
      }, PORT_CHECK_MS);
    };
    this._dialogClosed = () => clearTimeout(portTimer);
    this._dialogAction = async (r, button) => {
      if (r === "use-port") {
        const input = dialog.querySelector("[data-var=APP_PORT]");
        input.value = button.dataset.port;
        input.dispatchEvent(new Event("input", { bubbles: true }));
        return;
      }
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
      const name = dialog.querySelector("#app-name").value;
      const installed = await this._save(dialog, button, "Installing…", () =>
        this._call("install", { store_app: storeId, name, values }, true),
      );
      if (installed) {
        this._toast(`${app.name} installed`);
        this._setTab("apps");
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
      const gen = this._logGen;
      output.textContent = "";
      let lines = 0;
      const unsubscribe = await this._hass.connection.subscribeMessage(
        (msg) => {
          if (msg.lines) {
            // the first batch is the tail: always show its end
            const atBottom = lines === 0 || output.scrollHeight - output.scrollTop - output.clientHeight < 40;
            output.textContent += `${msg.lines.join("\n").replace(ANSI_CODES, "")}\n`;
            lines += msg.lines.length;
            if (lines > LOG_LINES_MAX) {
              output.textContent = output.textContent.split("\n").slice(-LOG_LINES_KEEP).join("\n");
              lines = LOG_LINES_KEEP;
            }
            if (atBottom && dialog.querySelector("#log-follow").checked) output.scrollTop = output.scrollHeight;
          }
          if (msg.error) output.textContent += `\n[error] ${msg.error}\n`;
          if (msg.end) output.textContent += "\n[container stopped]\n";
        },
        { type: `${DOMAIN}/logs/subscribe`, container: key, tail: 300 },
      );
      // another container was chosen, or the dialog closed, while subscribing
      if (gen !== this._logGen) unsubscribe();
      else this._unsubLogs = unsubscribe;
    };
    dialog.onchange = (ev) => {
      if (ev.target.id === "log-container") start(ev.target.value);
    };
    this._dialogAction = (r) => {
      if (r === "copy") navigator.clipboard?.writeText(output.textContent);
    };
    this._dialogClosed = () => this._stopLogs();
    start(keys[0]);
  }

  _stopLogs() {
    this._logGen += 1;
    this._unsubLogs?.();
    this._unsubLogs = null;
  }
}

// the page can load the module again after Home Assistant restarts
if (!customElements.get("docker-apps-panel")) customElements.define("docker-apps-panel", DockerAppsPanel);
