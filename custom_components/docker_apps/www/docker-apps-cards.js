// Dashboard card "Docker Apps" (type: custom:docker-apps-card): an app launcher.
// It shows the apps with "Show in app launcher" on, so store installs show up by themselves.
// It reads the app status sensors, so it works for every user, not only admins.

const esc = (value) =>
  String(value ?? "").replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" })[c]);

// states worth showing under an app; the text is the translated state of the status sensor
const SHOWN_STATES = new Set(["partial", "starting", "unhealthy", "stopped", "updating"]);

// the card's own texts; other languages fall back to English
const TEXTS = {
  en: { none: "No apps", open: "Open", title: "Title", show_stopped: "Show stopped apps", show_usage: "Show CPU and memory" },
  // cspell:disable
  bg: {
    none: "Няма приложения",
    open: "Отвори",
    title: "Заглавие",
    show_stopped: "Показвай спрените приложения",
    show_usage: "Показвай процесор и памет",
  },
  // cspell:enable
};
const text = (hass, key) => (TEXTS[hass?.language] || TEXTS.en)[key];

const STYLE = `
  .apps { display: grid; gap: 12px; grid-template-columns: repeat(auto-fill, minmax(var(--docker-app-width, 120px), 1fr)); }
  .title { font-size: 16px; font-weight: 500; padding: 0 4px 12px; }
  .app { position: relative; display: flex; flex-direction: column; align-items: center; gap: 8px; padding: 16px 8px 14px;
    border-radius: var(--ha-card-border-radius, 12px); background: var(--ha-card-background, var(--card-background-color));
    border: 1px solid var(--ha-card-border-color, var(--divider-color)); cursor: pointer; text-align: center;
    min-width: 0; box-sizing: border-box; transition: box-shadow .15s; font: inherit; color: inherit; text-decoration: none; }
  .app:hover { box-shadow: 0 2px 10px rgba(0,0,0,.12); }
  .app:focus-visible { outline: 2px solid var(--primary-color); }
  .bubble { width: 52px; height: 52px; border-radius: 50%; display: grid; place-items: center;
    background: rgba(var(--rgb-primary-color, 3, 169, 244), .15); color: var(--primary-color); overflow: hidden; }
  .bubble img { width: 34px; height: 34px; object-fit: contain; }
  .bubble ha-icon { --mdc-icon-size: 28px; }
  .app.off .bubble { filter: grayscale(1); opacity: .6; }
  .app.bad .bubble { background: rgba(219, 68, 55, .15); color: var(--error-color, #db4437); }
  .name { font-weight: 600; font-size: 15px; max-width: 100%; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
  .status { font-size: 12px; color: var(--secondary-text-color); white-space: nowrap; margin-top: -4px; }
  .empty { color: var(--secondary-text-color); padding: 8px 4px; }
`;

// "device|cpu" -> entity id of the app CPU and memory sensors; built again only when the
// entity registry changes, since hass changes with every state of every entity
let usageIndex = { entities: null, ids: {} };

function usageSensors(hass) {
  if (usageIndex.entities !== hass.entities) {
    const ids = {};
    for (const e of Object.values(hass.entities || {})) {
      if (e.platform === "docker_apps" && (e.translation_key === "cpu" || e.translation_key === "memory")) {
        ids[`${e.device_id}|${e.translation_key}`] = e.entity_id;
      }
    }
    usageIndex = { entities: hass.entities, ids };
  }
  return usageIndex.ids;
}

function usageState(hass, device, key) {
  const state = hass.states[usageSensors(hass)[`${device}|${key}`]];
  return state && !Number.isNaN(Number(state.state)) ? state : null;
}

function usage(hass, device) {
  const cpu = usageState(hass, device, "cpu");
  const memory = usageState(hass, device, "memory");
  const parts = [];
  if (cpu) parts.push(`CPU ${Math.round(Number(cpu.state))}%`);
  if (memory) parts.push(hass.formatEntityState?.(memory) ?? `${memory.state} ${memory.attributes.unit_of_measurement || ""}`);
  return parts.join(" · ");
}

// the line under the name: a state worth knowing (stopped, updating, ...) comes before the usage
function appLine(hass, status, device, showUsage) {
  if (SHOWN_STATES.has(status.state)) return hass.formatEntityState?.(status) ?? status.state;
  return showUsage ? usage(hass, device) : "";
}

function appInfo(hass, status, showUsage) {
  const device = hass.entities?.[status.entity_id]?.device_id;
  const { docker_app: key, entity_picture: picture, web_port: port, web_path: path, web_scheme: scheme } = status.attributes;
  return {
    id: status.entity_id,
    name: hass.devices?.[device]?.name_by_user || hass.devices?.[device]?.name || key,
    picture,
    state: status.state,
    line: appLine(hass, status, device, showUsage),
    url: port ? `${scheme || "http"}://${location.hostname}:${port}${path}` : null,
  };
}

const STATE_CLASS = { stopped: "off", unhealthy: "bad" };

// a link to the web UI; an app without one is a button that opens its more-info dialog
function renderApp(app, open) {
  const icon = app.picture ? `<img src="${esc(app.picture)}" alt="">` : `<ha-icon icon="mdi:docker"></ha-icon>`;
  const body = `<div class="bubble">${icon}</div>
      <div class="name">${esc(app.name)}</div>
      ${app.line ? `<div class="status">${esc(app.line)}</div>` : ""}`;
  const cls = `app ${STATE_CLASS[app.state] || ""}`;
  return app.url
    ? `<a class="${cls}" href="${esc(app.url)}" target="_blank" rel="noopener" title="${esc(`${open} ${app.name}`)}">${body}</a>`
    : `<button class="${cls}" data-id="${esc(app.id)}" title="${esc(app.name)}">${body}</button>`;
}

// status sensors of the apps that are in the launcher
const launcherApps = (hass) => Object.values(hass.states).filter((s) => s.attributes.docker_app && s.attributes.launcher);

class DockerAppsCard extends HTMLElement {
  static getStubConfig() {
    return { title: "Apps", show_stopped: true, show_usage: false };
  }

  static getConfigElement() {
    return document.createElement("docker-apps-card-editor");
  }

  setConfig(config) {
    this._config = config || {};
    this._signature = null;
    if (this._hass) this.hass = this._hass;
  }

  set hass(hass) {
    this._hass = hass;
    const { exclude = [], show_stopped: showStopped = true, show_usage: showUsage = false } = this._config;
    const hidden = new Set(exclude);
    const apps = launcherApps(hass)
      .filter((s) => !hidden.has(s.attributes.docker_app) && (showStopped || s.state !== "stopped"))
      .map((s) => appInfo(hass, s, showUsage))
      .sort((a, b) => a.name.localeCompare(b.name));
    const signature = JSON.stringify(apps);
    if (signature !== this._signature) {
      this._signature = signature;
      this._apps = apps;
      this._render();
    }
  }

  _render() {
    if (!this.shadowRoot) {
      this.attachShadow({ mode: "open" });
      this.shadowRoot.addEventListener("click", (ev) => this._moreInfo(ev));
    }
    const title = this._config.title ? `<div class="title">${esc(this._config.title)}</div>` : "";
    const open = text(this._hass, "open");
    const body = this._apps.length
      ? `<div class="apps">${this._apps.map((app) => renderApp(app, open)).join("")}</div>`
      : `<div class="empty">${text(this._hass, "none")}</div>`;
    this.shadowRoot.innerHTML = `<style>${STYLE}</style>${title}${body}`;
  }

  _moreInfo(ev) {
    const entityId = ev.target.closest("button.app")?.dataset.id;
    if (entityId) this.dispatchEvent(new CustomEvent("hass-more-info", { detail: { entityId }, bubbles: true, composed: true }));
  }

  getCardSize() {
    return 3;
  }

  getGridOptions() {
    return { columns: "full", rows: "auto" };
  }
}

const SCHEMA = [
  { name: "title", selector: { text: {} } },
  { name: "show_stopped", default: true, selector: { boolean: {} } },
  { name: "show_usage", default: false, selector: { boolean: {} } },
];

// visual editor: Home Assistant's own form; which apps show is the
// "Show in app launcher" option of each app
class DockerAppsCardEditor extends HTMLElement {
  setConfig(config) {
    this._config = config;
    this._render();
  }

  set hass(hass) {
    const first = !this._hass;
    this._hass = hass;
    if (first) this._render();
    else if (this._form) this._form.hass = hass;
  }

  _render() {
    if (!this._hass || !this._config) return;
    if (!this._form) {
      this._form = document.createElement("ha-form");
      this._form.computeLabel = (field) => text(this._hass, field.name);
      this._form.addEventListener("value-changed", (ev) => {
        this.dispatchEvent(new CustomEvent("config-changed", { detail: { config: ev.detail.value }, bubbles: true, composed: true }));
      });
      this.appendChild(this._form);
    }
    this._form.hass = this._hass;
    this._form.schema = SCHEMA;
    this._form.data = { show_stopped: true, show_usage: false, ...this._config };
  }
}

// the frontend can load the module again after Home Assistant restarts
if (!customElements.get("docker-apps-card")) customElements.define("docker-apps-card", DockerAppsCard);
if (!customElements.get("docker-apps-card-editor")) customElements.define("docker-apps-card-editor", DockerAppsCardEditor);

window.customCards = window.customCards || [];
if (!window.customCards.some((c) => c.type === "docker-apps-card")) {
  window.customCards.push({
    type: "docker-apps-card",
    name: "Docker Apps",
    description: "Buttons that open your apps; apps installed from a store show up by themselves.",
    preview: true,
  });
}
