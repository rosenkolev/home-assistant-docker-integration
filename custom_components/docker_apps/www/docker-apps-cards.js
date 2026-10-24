// Dashboard card "Docker Apps" (type: custom:docker-apps-card): an app launcher.
// It shows the apps with "Show in app launcher" on, so store installs show up by themselves.
// It reads the app status sensors, so it works for every user, not only admins.

const esc = (value) =>
  String(value ?? "").replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" })[c]);

const STATUS = {
  partial: "Partly running",
  starting: "Starting",
  unhealthy: "Unhealthy",
  stopped: "Stopped",
  updating: "Updating…",
};

const STYLE = `
  .apps { display: grid; gap: 12px; grid-template-columns: repeat(auto-fill, minmax(var(--docker-app-width, 120px), 1fr)); }
  .title { font-size: 16px; font-weight: 500; padding: 0 4px 12px; }
  .app { position: relative; display: flex; flex-direction: column; align-items: center; gap: 8px; padding: 16px 8px 14px;
    border-radius: var(--ha-card-border-radius, 12px); background: var(--ha-card-background, var(--card-background-color));
    border: 1px solid var(--ha-card-border-color, var(--divider-color)); cursor: pointer; text-align: center;
    min-width: 0; box-sizing: border-box; transition: box-shadow .15s; }
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

function appInfo(hass, status) {
  const device = hass.entities?.[status.entity_id]?.device_id;
  const { docker_app: key, entity_picture: picture, web_port: port, web_path: path, web_scheme: scheme } = status.attributes;
  return {
    id: status.entity_id,
    name: hass.devices?.[device]?.name_by_user || hass.devices?.[device]?.name || key,
    picture,
    state: status.state,
    url: port ? `${scheme || "http"}://${location.hostname}:${port}${path || "/"}` : null,
  };
}

function renderApp(app) {
  const icon = app.picture ? `<img src="${esc(app.picture)}" alt="">` : `<ha-icon icon="mdi:docker"></ha-icon>`;
  const cls = app.state === "stopped" ? "off" : app.state === "unhealthy" ? "bad" : "";
  const status = STATUS[app.state];
  return `<div class="app ${cls}" role="button" tabindex="0" data-id="${esc(app.id)}" title="${esc(app.url ? `Open ${app.name}` : app.name)}">
      <div class="bubble">${icon}</div>
      <div class="name">${esc(app.name)}</div>
      ${status ? `<div class="status">${esc(status)}</div>` : ""}
    </div>`;
}

class DockerAppsCard extends HTMLElement {
  static getStubConfig() {
    return { title: "Apps" };
  }

  setConfig(config) {
    this._config = config || {};
    this._signature = null;
  }

  set hass(hass) {
    this._hass = hass;
    const exclude = new Set(this._config.exclude || []);
    const apps = Object.values(hass.states)
      .filter((s) => s.attributes.docker_app && s.attributes.launcher && !exclude.has(s.attributes.docker_app))
      .map((s) => appInfo(hass, s))
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
      this.shadowRoot.addEventListener("click", (ev) => this._open(ev));
      this.shadowRoot.addEventListener("keydown", (ev) => ev.key === "Enter" && this._open(ev));
    }
    const title = this._config.title ? `<div class="title">${esc(this._config.title)}</div>` : "";
    const body = this._apps.length ? `<div class="apps">${this._apps.map(renderApp).join("")}</div>` : `<div class="empty">No apps</div>`;
    this.shadowRoot.innerHTML = `<style>${STYLE}</style>${title}${body}`;
  }

  _open(ev) {
    const app = this._apps.find((a) => a.id === ev.target.closest(".app")?.dataset.id);
    if (!app) return;
    if (app.url) {
      window.open(app.url, "_blank", "noopener");
      return;
    }
    this.dispatchEvent(new CustomEvent("hass-more-info", { detail: { entityId: app.id }, bubbles: true, composed: true }));
  }

  getCardSize() {
    return 3;
  }

  getGridOptions() {
    return { columns: "full", rows: "auto" };
  }
}

// the frontend can load the module again after Home Assistant restarts
if (!customElements.get("docker-apps-card")) customElements.define("docker-apps-card", DockerAppsCard);

window.customCards = window.customCards || [];
if (!window.customCards.some((c) => c.type === "docker-apps-card")) {
  window.customCards.push({
    type: "docker-apps-card",
    name: "Docker Apps",
    description: "Buttons that open your apps; apps installed from a store show up by themselves.",
    preview: true,
  });
}
