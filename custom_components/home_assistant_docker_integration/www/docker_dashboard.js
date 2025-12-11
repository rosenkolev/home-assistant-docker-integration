/**
 * @import { HomeAssistant } from './home-assistant'
 */

import {
  LitElement,
  html,
  css,
  nothing,
} from "https://unpkg.com/lit-element@4.2.1/lit-element.js?module";

const DOMAIN = "docker_integration";

function _assert_element_config(config, config_keys) {
  if (config_keys) {
    if (!config) throw new Error("No configurations found");
    for (const key of config_keys) {
      if (!config[key]) throw new Error("No configuration " + key);
    }
  }
}

function fireEvent(element, type, detail) {
  const event = new Event(type, {
    bubbles: true,
    cancelable: false,
    composed: true,
  });
  event.detail = detail;
  element.dispatchEvent(event);
}

function showDialog(element, dialog, params) {
  fireEvent(element, "show-dialog", {
    dialogTag: dialog,
    dialogImport: () => Promise.resolve(),
    dialogParams: params || {},
  });
}

function toList(value) {
  return value
    ? value
      .split(",")
      .map((v) => v.trim())
      .filter((v) => v)
    : undefined;
}

const d_call = (hass, service, data) => hass.callService(DOMAIN, service, data);
const r_badge = (state, on, off) => html`<ha-assist-chip class="${state ? "badge-on" : "badge-off"}" .label=${state ? on : off}></ha-assist-chip>`;

class BaseDialogLitElement extends LitElement {
  static get properties() {
    return {
      hass: { attribute: false },
      _dialogParams: { state: true },
    };
  }

  static styles = [
    css`
      mwc-button { cursor: pointer; }
    `,
  ];

  renderContent() { return nothing; }
  get heading() { return null; }

  async showDialog(params) {
    this._dialogParams = params;
    await this.updateComplete;
  }

  closeDialog() {
    this._dialogParams = undefined;
    fireEvent(this, "dialog-closed", { dialog: this.localName });
  }

  render() {
    if (!this.hass || !this._dialogParams) {
      return nothing;
    }

    const title = this.heading;
    return html`
      <ha-dialog
        open
        scrimClickAction
        escapeKeyAction
        .heading=${title ? html`
          <header class="header_title">
            <mwc-icon-button
              aria-label="Close"
              title="Close"
              dialogAction="close"
              class="header_button"
            >
              <ha-icon icon="mdi:close"></ha-icon>
            </mwc-icon-button>
            <span role="heading">${title}</span>
          </header>
        ` : nothing}
        @closed=${this.closeDialog}
      >
        ${this.renderContent()}
      </ha-dialog>
    `;
  }
}

class DockerAddDialog extends BaseDialogLitElement {
  static SCHEMA = [
    { name: "image", selector: { text: {} } },
    { name: "name", selector: { text: {} } },
    { name: "network", selector: { text: {} } },
    {
      name: "ports",
      label: "Ports (80:80, 443:443)",
      selector: { text: {} },
    },
    {
      name: "volumes",
      label: "Volumes (/host:/container)",
      selector: { text: {} },
    },
    {
      name: "restart_policy",
      label: "Restart Policy",
      selector: {
        select: {
          mode: "dropdown",
          options: [
            { value: "no", label: "No" },
            { value: "always", label: "Always" },
            { value: "on-failure", label: "On Failure" },
            { value: "unless-stopped", label: "Unless Stopped" },
          ],
        },
      },
    },
  ];

  get heading() { return "Add Container"; }

  async showDialog(dialogParams) {
    this._formData = {
      image: "",
      name: "",
      network: "",
      ports: "",
      volumes: "",
      restart_policy: "no",
    };
    super.showDialog(dialogParams);
  }

  closeDialog() {
    this._formData = undefined;
    super.closeDialog();
  }

  renderContent() {
    const add = () => {
      const data = this._formData;
      const portsList = toList(data.ports);
      const volumesList = toList(data.volumes);
      d_call(this.hass, "create", {
        image: data.image,
        name: data.name,
        network: data.network || undefined,
        ports: portsList,
        volumes: volumesList,
        restart_policy: data.restart_policy || undefined,
      });
      this.closeDialog();
    };

    return html`
        <div class="content">
          <ha-form
            .hass=${this.hass}
            .data=${this._formData}
            .schema=${DockerAddDialog.SCHEMA}
            @value-changed=${(ev) => (this._formData = ev.detail.value)}
          ></ha-form>
        </div>
        <mwc-button slot="secondaryAction" @click=${this.closeDialog}>Close</mwc-button>
        <mwc-button slot="primaryAction" @click=${add}>Add</mwc-button>
    `;
  }

  static styles = [
    ...BaseDialogLitElement.styles,
    css`
      .content {
        width: 400px;
      }
      @media all and (max-width: 450px) {
        .content {
          width: 100%;
        }
      }
    `,
  ];
}

class DockerLogsDialog extends BaseDialogLitElement {
  static get properties() {
    return {
      ...super.properties,
      _logs: { state: true },
    };
  }

  get heading() { return "Container Logs"; }
  _logs = "Loading...";

  async async_fetch_logs(id) {
    try {
      const response = await this.hass.callService(
        "docker_integration",
        "logs",
        { id: this._dialogParams.id },
        /** target */undefined,
        /** notifyOnError  */ true,
        /** returnResponse */ true
      );
      this._logs = response.response.logs;
    } catch (e) {
      this._logs = "Error fetching logs: " + e.message;
    }
  }

  async showDialog(dialogParams) {
    await super.showDialog(dialogParams);
    this.async_fetch_logs();
  }

  closeDialog() {
    this._logs = undefined;
    super.closeDialog();
  }

  renderContent() {
    return html`
      <div class="content"><pre>${this._logs}</pre></div>
      <mwc-button slot="primaryAction" @click=${this.closeDialog}>Close</mwc-button>
    `;
  }

  static styles = [
    ...BaseDialogLitElement.styles,
    css`
      ha-dialog {
        --mdc-dialog-min-width: 80vw;
        --mdc-dialog-max-width: 80vw;
      }
      .content {
        width: 100%;
        overflow-x: scroll;
      }
      pre {
        font-family: monospace;
        white-space: pre-wrap;
      }
    `,
  ];
}

class ConfirmDialog extends BaseDialogLitElement {
  get heading() { return this._dialogParams.title; }

  renderContent() {
    const ok = () => {
      this._dialogParams.callback();
      this.closeDialog();
    };
    return html`
      <div class="content">${this._dialogParams.message}</div>
      <mwc-button slot="primaryAction" @click=${this.closeDialog}>Close</mwc-button>
      <mwc-button slot="secondaryAction" @click=${() => ok()}>Yes</mwc-button>
    `;
  }
}

function confirmDialog(element, title, message, callback) {
  showDialog(element, "docker-confirm-dialog", { title, message, callback });
}

function find_entities_by_model(devices, entities, model) {
  const device_id = devices.find((it) => it.model == model).id;
  return entities
    .filter((it) => it.device_id === device_id)
    .map((it) => ({ name: it.name || it.original_name, entity_id: it.entity_id }));
}

const filter_items = (items, showInactive, hass, state) => {
  return showInactive
    ? items
    : items.filter((it) => hass.states[it.entity_id].state === state);
}

class BaseFullWidthLitElement extends LitElement {
  static get properties() {
    return {
      hass: { attribute: false },
      config: { state: true },
    };
  }

  /** Override this methods */
  renderContent() { return nothing; }
  verifyConfig(config) { }

  setConfig(config) {
    this.verifyConfig(config);
    this.config = config;
  }

  render() { return !this.config || !this.hass ? nothing : this.renderContent(); }
  getCardSize() { return 12; }
  getGridOptions() { return { columns: "full" }; }
}

class HaDiGrid extends BaseFullWidthLitElement {
  static styles = css`
    .header {
      background: none;
      backdrop-filter: none;
      -webkit-backdrop-filter: none;
      border: none;
      box-shadow: none;
      padding: 0;
      display: flex;
      justify-content: space-between;
      align-items: center;
      margin-top: 15px;
      margin-bottom: 10px;
    }
    .title {
      margin-left: 10px;
      color: var(--secondary-text-color);
      font-size: var(--ha-font-size-m);
      font-weight: var(--ha-font-weight-medium);
    }
    .controls {
      display: flex;
      align-items: center;
      gap: 20px;
    }
    .grid-header {
      grid-template-columns: var(--hadi-grid-column-widths);
      display: grid;
      padding: 8px 10px;
      gap: 10px;
      border-top: 3px double var(--divider-color);
      font-size: var(--ha-font-size-m);
      font-weight: var(--ha-font-weight-medium);
      color: var(--primary-text-color);
    }
    .grid-content {
      display: flex;
      flex-direction: column;
      gap: 8px 10px;
    }
    .group-items {
      display: flex;
      flex-direction: column;
      gap: 0 10px;
      padding: 8px 10px;
    }
    [role=row] {
      grid-template-columns: var(--hadi-grid-column-widths);
      border-top: 1px solid var(--divider-color);
    }
  `;

  getItems() { return this.config?.items || []; }
  groupBy() { return null; }
  renderControls() { return nothing; }
  renderRow(item) { return nothing; }
  columnWidths = null;
  gridHeaders = null;

  renderContent() {
    const headers = this.gridHeaders;
    const items = this.getItems();
    const groups = this.groupBy();
    const renderList = groups && groups.length === items.length
      ? this._getGroupedRenderList(items, groups)
      : items.map((item) => ({ type: 'item', data: item }));

    return html`
      <style>
        :host {
          --expansion-panel-content-padding: 0;
          --hadi-grid-column-widths: ${this.columnWidths};
        }
      </style>
      <ha-card class="header">
        <div class="title">${this.config.title}</div>
        <div class="controls">
          ${this.renderControls()}
        </div>
      </ha-card>
      ${headers ? html`
        <header role="header" class="grid-header">
          ${headers.map((header) => html`<div role="cell">${header}</div>`)}
        </header>
      ` : nothing}
      <div class="grid-content">
        ${renderList.map((entry) => entry.type === "item"
      ? this.renderRow(entry.data)
      : html`
            <ha-expansion-panel .header="${entry.key}" outlined>
              <div class="group-items">
                ${entry.items.map((item) => this.renderRow(item))}
              </div>
            </ha-expansion-panel>
          `)}
      </div>
    `;
  }

  _getGroupedRenderList(items, groups) {
    const groupedContent = new Map();
    const renderList = [];
    items.forEach((item, index) => {
      const groupKey = groups[index];
      if (groupKey === null || groupKey === undefined) {
        renderList.push({ type: 'item', data: item });
      } else {
        if (!groupedContent.has(groupKey)) {
          const items = [item];
          groupedContent.set(groupKey, items);
          renderList.push({ type: 'group', key: groupKey, items });
        } else {
          groupedContent.get(groupKey).push(item);
        }
      }
    });

    return renderList;
  }
}

class HaDiRow extends BaseFullWidthLitElement {
  renderRow() { return nothing; }

  verifyConfig(config) {
    _assert_element_config(config, ["entity_id", "name"]);
    this.tracked_state_keys = [config.entity_id];
  }

  /** @type {HomeAssistant} */ hass;
  /** @type {string[]} */ tracked_state_keys;

  shouldUpdate(changedProps) {
    if (changedProps.has("config")) {
      return true;
    }
    if (this.tracked_state_keys && changedProps.has("hass")) {
      const oldHass = changedProps.get("hass");
      if (!oldHass) return true;
      return this.tracked_state_keys.some(
        (key) => oldHass.states[key] !== this.hass.states[key]
      );
    }
    return true;
  }

  renderContent() {
    this.setAttribute("role", "row");
    return this.renderRow();
  }

  static styles = css`
    :host {
      display: grid;
      align-items: center;
      padding: 0 10px;
      min-height: 50px;
      gap: 0 10px;
    }
    .badge-on {
      background-color: var(--primary-background-color);
    }
    .badge-off {
      background-color: var(--secondary-background-color);
    }
    [role="cell"] {
      overflow: hidden;
      text-overflow: ellipsis;
      white-space: nowrap;
    }
    [role="cell"].controls {
      overflow: visible;
    }
  `;
}

class DockerVolumeRow extends HaDiRow {
  renderRow() {
    const state = this.hass.states[this.config.entity_id];
    const used = state.state === "on";
    return html`
        <div role="cell">${this.config.name}</div>
        <div role="cell">${r_badge(used, "In use", "Not used")}</div>
        <div role="cell">${state.attributes.mount}</div>
        <div role="cell">${state.attributes.size}</div>
    `;
  }
}

class DockerVolumeGrid extends HaDiGrid {
  static get properties() {
    return {
      ...BaseFullWidthLitElement.properties,
      showInactiveContainers: { state: true, type: Boolean },
    };
  }

  showInactiveContainers = false;
  gridHeaders = ["Name", "Used", "Mount", "Size"];
  columnWidths = "32% 82px 35% 10%";
  getItems = () => filter_items(this.config.items, this.showInactiveContainers, this.hass, "on");

  renderControls() {
    const onShowInactiveContainers = (event) => {
      this.showInactiveContainers = event.target.checked;
      this.requestUpdate();
    };

    return html`
      <ha-formfield label="Show inactive volumes">
        <ha-switch
          .checked=${this.showInactiveContainers}
          @change=${onShowInactiveContainers}
        ></ha-switch>
      </ha-formfield>
      <ha-button size="small" @click=${() => this.hass.callService(DOMAIN, "prune_volumes")}>
        <ha-icon icon="mdi:delete"></ha-icon>
        Prune Volumes
      </ha-button>
    `;
  }

  renderRow(item) {
    return html`
      <docker-volume-row .hass=${this.hass} .config=${item}></docker-volume-row>
    `;
  }
}

class DockerImageRow extends HaDiRow {
  renderRow() {
    const state = this.hass.states[this.config.entity_id];
    const used = state.state === "on";
    return html`
        <div role="cell">${this.config.name}</div>
        <div role="cell">${r_badge(used, "In use", "Not used")}</div>
        <div role="cell">${state.attributes.description}</div>
    `;
  }
}

class DockerImageGrid extends HaDiGrid {
  static get properties() {
    return {
      ...BaseFullWidthLitElement.properties,
      showInactiveContainers: { state: true, type: Boolean },
    };
  }

  showInactiveContainers = false;
  getItems = () => filter_items(this.config.items, this.showInactiveContainers, this.hass, "on");
  gridHeaders = ["Name", "Used", "Description"];
  columnWidths = "32% 82px 40%";

  renderControls() {
    const onShowInactiveContainers = (event) => {
      this.showInactiveContainers = event.target.checked;
      this.requestUpdate();
    };
    return html`
      <ha-formfield label="Show inactive volumes">
        <ha-switch
          .checked=${this.showInactiveContainers}
          @change=${onShowInactiveContainers}
        ></ha-switch>
      </ha-formfield>
      <ha-button size="small" @click=${() => d_call(this.hass, "prune_images")}>
        <ha-icon icon="mdi:delete"></ha-icon>
        Prune Images
      </ha-button>
    `;
  }

  renderRow(item) {
    return html`
      <docker-image-row .hass=${this.hass} .config=${item}></docker-image-row>
    `;
  }
}

class DockerContainerGrid extends HaDiGrid {
  static get properties() {
    return {
      ...BaseFullWidthLitElement.properties,
      showInactiveContainers: { state: true, type: Boolean },
    };
  }

  showInactiveContainers = false;
  getItems = () => filter_items(this.config.items, this.showInactiveContainers, this.hass, "running");
  groupBy = () => this.getItems().map(item => this.hass.states[item.entity_id]?.attributes.project || null);
  gridHeaders = ["", "State", "Status", "Ports", "Actions"];
  columnWidths = "20% 100px 150px auto 60px";

  renderControls() {
    const onShowInactiveContainers = (event) => {
      this.showInactiveContainers = event.target.checked;
      this.requestUpdate();
    };
    return html`
      <ha-formfield label="Show inactive volumes">
        <ha-switch
          .checked=${this.showInactiveContainers}
          @change=${onShowInactiveContainers}
        ></ha-switch>
      </ha-formfield>
      <ha-button size="small" @click=${() => showDialog(this, 'docker-create-container-dialog')}>
        <ha-icon icon="mdi:plus"></ha-icon>
        Create
      </ha-button>
      <ha-button size="small" @click=${() => this.hass.callService(DOMAIN, "prune_containers")}>
        <ha-icon icon="mdi:delete"></ha-icon>
        Prune Containers
      </ha-button>
    `;
  }

  renderRow(item) {
    return html`<docker-container-row .hass=${this.hass} .config=${item}></docker-container-row>`;
  }
}

class DockerContainerRow extends HaDiRow {
  renderRow() {
    const state = this.hass.states[this.config.entity_id];
    const id = state.attributes.sid;
    const isRunning = state.state === "running";
    const actions = [
      {
        label: "Start",
        icon: "mdi:play",
        action: () => d_call(this.hass, 'start', { id }),
        visible: !isRunning,
      },
      {
        label: "Stop",
        icon: "mdi:stop",
        action: () => confirmDialog(this, "Stop Container", "Are you sure you want to stop this container?", () => d_call(this.hass, 'stop', { id })),
        visible: isRunning,
      },
      {
        label: "Restart",
        icon: "mdi:restart",
        action: () => d_call(this.hass, 'restart', { id }),
        visible: isRunning,
      },
      {
        label: "Remove",
        icon: "mdi:delete",
        action: () => confirmDialog(this, "Remove Container", "Are you sure you want to remove this container?", () => d_call(this.hass, 'remove', { id })),
        visible: !isRunning,
      },
      {
        label: "Logs",
        icon: "mdi:text",
        action: () => showDialog(this, "docker-logs-dialog", { id }),
        visible: true,
      },
    ].filter((a) => a.visible);

    return html`
        <div role="cell" class="card-title">
          <div class="card-name">${this.config.name}</div>
          <div class="card-id">${state.attributes.sid}</div>
        </div>
        <div role="cell">${r_badge(isRunning, "Running", "Not running")}</div>
        <div role="cell">${state.attributes.status}</div>
        <div role="cell">
          <ha-chip-set class="ports">
            ${state.attributes.ports ? state.attributes.ports.map((port) => html`
              <ha-assist-chip class="port" .label="${port}"></ha-assist-chip>
            `) : nothing}
          </ha-chip-set>
        </div>
        <div role="cell" class="controls">
          <ha-button-menu corner="BOTTOM_END">
            <ha-icon-button
              slot="trigger"
              .label="Actions"
            >
              <ha-icon icon="mdi:dots-vertical"></ha-icon>
            </ha-icon-button>
            ${actions.map((action) => html`
              <mwc-list-item graphic="icon" @click=${action.action}>
                <ha-icon slot="graphic" .icon=${action.icon}></ha-icon>
                ${action.label}
              </mwc-list-item>
            `)}
            </ha-button-menu>
        </div>
    `;
  }

  static get styles() {
    return css`
      ${HaDiRow.styles}
      .card-id {
        line-height: 1.2;
        font-size: 12px;
        color: var(--secondary-text-color);
      }
    `;
  }
}

class StrategyViewDockerContainers {
  static async generate(config, hass) {
    const [devices, entities] = await Promise.all([
      hass.callWS({ type: "config/device_registry/list" }),
      hass.callWS({ type: "config/entity_registry/list" }),
    ]);

    const containers = devices
      .filter((it) => it.model == "container")
      .map((it) => ({ name: it.name, id: it.identifiers[0][1] }))
      .map((it) => ({ ...it, entity_id: `sensor.docker_integration_containers_${it.id}` }));
    const volumes = find_entities_by_model(devices, entities, "volume").filter((it) => it.entity_id.startsWith("binary_sensor."));
    const images = find_entities_by_model(devices, entities, "image").filter((it) => it.entity_id.startsWith("binary_sensor."));

    return {
      sections: [
        {
          type: "grid",
          column_span: 4,
          cards: [
            {
              type: "custom:docker-container-grid",
              title: "Containers",
              items: containers,
            },
            {
              type: "custom:docker-image-grid",
              title: "Images",
              items: images,
            },
            {
              type: "custom:docker-volume-grid",
              title: "Volumes",
              items: volumes,
            },
          ],
        },
      ],
    };
  }
}

customElements.define("docker-create-container-dialog", DockerAddDialog);
customElements.define("docker-logs-dialog", DockerLogsDialog);
customElements.define("docker-confirm-dialog", ConfirmDialog);

customElements.define("docker-container-grid", DockerContainerGrid);
customElements.define("docker-container-row", DockerContainerRow);

customElements.define("docker-image-grid", DockerImageGrid);
customElements.define("docker-image-row", DockerImageRow);

customElements.define("docker-volume-grid", DockerVolumeGrid);
customElements.define("docker-volume-row", DockerVolumeRow);

customElements.define(
  "ll-strategy-view-docker-containers",
  StrategyViewDockerContainers
);
