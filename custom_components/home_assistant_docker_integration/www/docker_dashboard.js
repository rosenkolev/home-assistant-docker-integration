/**
 * @import { HomeAssistant } from './home-assistant'
 * @import { HassEntity } from './home-assistant-js-websocket';
 */

import {
  LitElement,
  html,
  css,
  nothing,
} from "https://unpkg.com/lit-element@4.2.1/lit-element.js?module";

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

const dialogHeading = (title) => html`
  <div class="header_title">
    <ha-icon-button
      .label="Close"
      dialogAction="close"
      class="header_button"
    ><ha-icon .icon="mdi:close"><ha-icon></ha-icon-button>
    <span>${title}</span>
  </div>
`;

class BaseRowLitElement extends LitElement {
  static get properties() {
    return {
      hass: { attribute: false },
      config: { state: true },
    };
  }

  /** @type {HomeAssistant} */ hass;
  /** @type {string[]} */ tracked_state_keys;

  shouldUpdate(changedProps) {
    let hasChanged = false;
    if (this.config && this.hass) {
      let oldHass;
      hasChanged =
        changedProps.has("config") ||
        !(oldHass = changedProps.get("hass")) ||
        this.tracked_state_keys.some(
          (key) => oldHass.states[key] !== this.hass.states[key]
        ); // direct check
    }

    console.log("shouldUpdate:" + hasChanged);
    return hasChanged;
  }

  render() {
    if (!this.config || !this.hass) {
      return nothing;
    }

    return this.render_body();
  }

  getCardSize() {
    return 12;
  }
  getGridOptions() {
    return { columns: "full" };
  }

  static baseCss = css`
    .card-content {
      display: flex;
      padding: 5px 10px;
      align-items: center;
      gap: 20px;
    }
    .card-content > * {
      overflow: hidden;
      text-overflow: ellipsis;
    }
    .flex {
      flex: 1;
    }
    .inactive {
      background-color: var(--secondary-background-color);
    }
  `;
}

class DockerTitle extends BaseRowLitElement {
  /** @type {{ heading: string, actions: { type: 'button', icon?: string, name: string, action: { dialog?: string, event?: string, entity_id?: string } } }} */
  config;

  setConfig(config) {
    _assert_element_config(config, ['heading']);
    this.config = config;
  }

  render_body() {
    const handle_action = (action) => {
      if (action.dialog) {
        fireEvent(this, "show-dialog", {
          dialogTag: action.dialog,
          dialogImport: () => Promise.resolve(),
          dialogParams: {},
        });
      } else if (action.event) {
        const domain = action.entity_id.substring(0, action.entity_id.indexOf('.'));
        this.hass.callService(domain, action.event, { entity_id: action.entity_id });
      } else { throw new Error('No action') }
    };
    return html`
      <ha-card>
        <div class="title">${this.config.heading}</div>
        <div class="controls">
          ${this.config.actions.map(it => html`
             <ha-button size="small" @click=${() => handle_action(it.action)}>${it.icon ? html`<ha-icon .icon=${it.icon}></ha-icon>&nbsp;` : ''}${it.name}</ha-button>
            `)}
        </div>
      </ha-card>
    `
  }

  static styles = [
    css`
      ha-card {
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
      }
      .title {
        margin-left: 10px;
        color: var(--secondary-text-color);
        font-size: var(--ha-font-size-m);
        font-weight: var(--ha-font-weight-medium);
      }

    `
  ]
}

class DockerContainerCard extends BaseRowLitElement {
  /** @type {{ container_id: string, name: string }} */
  config;

  setConfig(config) {
    _assert_element_config(config, ["container_id", "name"]);
    this._key_switch = `switch.docker_integration_containers_${config.container_id}`;
    this._key_sensor = `sensor.docker_integration_containers_${config.container_id}`;
    this.tracked_state_keys = [this._key_switch, this._key_sensor];
    this.config = config;
  }

  render_body() {
    /** @type {HassEntity} */
    const sensor_state = this.hass.states[this._key_sensor];
    const switch_state = this.hass.states[this._key_switch];
    const id = this.config.container_id
    const isOn = switch_state.state === "on";
    const toggle = () =>
      this.hass.callService("homeassistant", "toggle", {
        entity_id: this._key_switch,
      });
    const restart = () =>
      this.hass.callService("button", "press", {
        entity_id: `button.docker_integration_containers_${id}_restart`,
      });
    return html`
      <ha-card>
        <div class="card-content">
          <div class="card-title">
            <div class="card-name">${this.config.name}</div>
            <div class="card-id">${sensor_state.attributes.sid}</div>
          </div>
          <ha-assist-chip
            class="col1 ${isOn ? "" : "inactive"}"
            .label="${sensor_state.state}"
          ></ha-assist-chip>
          <div class="col2">${sensor_state.attributes.status}</div>
          <ha-chip-set class="ports">
            ${sensor_state.attributes.ports.map((port) => html`
                <ha-assist-chip class="port" .label="${port}"></ha-assist-chip>
              `)}
          </ha-chip-set>
          <div class="flex"></div>
          <div class="control">
            <ha-switch .checked="${isOn}" @click=${() => toggle()}></ha-switch>
            <ha-button size="small" @click=${() => restart()}>Restart</ha-button>
          </div>
        </div>
      </ha-card>
    `;
  }

  static styles = [
    this.baseCss,
    css`
      .card-title {
        width: 20%;
      }
      .card-name {
        font-weight: 500;
      }
      .card-id {
        line-height: 1.2;
        font-size: 12px;
        color: var(--secondary-text-color);
      }
      .col1 {
        width: 78px;
      }
      .col2 {
        width: 150px;
      }
    `,
  ];
}

class DockerVolumeCard extends BaseRowLitElement {
  /** @type {{ entity_id: string }} */
  config;

  setConfig(config) {
    _assert_element_config(config, ["entity_id", "name"]);
    this.tracked_state_keys = [config.entity_id];
    this.config = config;
  }

  render_body() {
    const state = this.hass.states[this.config.entity_id];
    const used = state.state === "on";
    return html`
      <ha-card>
        <div class="card-content">
          <div class="col0">${this.config.name}</div>
          <ha-assist-chip
            class="col1 ${used ? "" : "inactive"}"
            .label="${used ? "In use" : "Not used"}"
          ></ha-assist-chip>
          <div class="col2">${state.attributes.mount}</div>
          <div class="col3">${state.attributes.size}</div>
          <div class="flex" />
          <div class="card-control"></div>
        </div>
      </ha-card>
    `;
  }

  static styles = [
    this.baseCss,
    css`
      .col0 {
        width: 32%;
      }
      .col1 {
        width: 82px;
      }
      .col2 {
        width: 35%;
      }
    `,
  ];
}

class DockerImageCard extends BaseRowLitElement {
  /** @type {{ entity_id: string }} */
  config;

  setConfig(config) {
    _assert_element_config(config, ["entity_id", "name"]);
    this.tracked_state_keys = [config.entity_id];
    this.config = config;
  }

  render_body() {
    const state = this.hass.states[this.config.entity_id];
    const used = state.state === "on";
    return html`
      <ha-card>
        <div class="card-content">
          <div class="col0">${this.config.name}</div>
          <ha-assist-chip
            class="col1 ${used ? "" : "inactive"}"
            .label="${used ? "In use" : "Not used"}"
          ></ha-assist-chip>
          <div class="col2">${state.attributes.description}</div>
          <div class="flex" />
          <div class="card-control"></div>
        </div>
      </ha-card>
    `;
  }

  // styles
  static styles = [
    this.baseCss,
    css`
      .col0 {
        width: 32%;
      }
      .col1 {
        width: 82px;
      }
      .col2 {
        width: 40%;
        max-height: 60px;
      }
    `,
  ];
}

class DockerAddDialog extends LitElement {
  static get properties() {
    return {
      hass: { attribute: false },
      data: { state: true },
    };
  }

  async showDialog(dialogParams) {
    this._dialogParams = dialogParams;
    await this.updateComplete;
  }

  closeDialog() {
    this._dialogParams = undefined;
    fireEvent(this, "dialog-closed", { dialog: this.localName });
  }

  render() {
    if (!this._dialogParams) {
      return nothing;
    }
    const add = () => {
      const image = this.shadowRoot.querySelector("#image").value;
      const name = this.shadowRoot.querySelector("#name").value;
      const network = this.shadowRoot.querySelector("#network").value;
      const portsStr = this.shadowRoot.querySelector("#ports").value;
      const volumesStr = this.shadowRoot.querySelector("#volumes").value;
      const restart = this.shadowRoot.querySelector("#restart").value;

      const ports = portsStr ? portsStr.split(",").map(p => p.trim()).filter(p => p) : undefined;
      const volumes = volumesStr ? volumesStr.split(",").map(v => v.trim()).filter(v => v) : undefined;

      const payload = {
        image,
        name,
        network: network || undefined,
        ports: ports,
        volumes: volumes,
        restart_policy: restart || undefined
      };

      this.hass.callService("docker_integration", "create", payload);
      this.closeDialog();
    };

    return html`
      <ha-dialog
        open
        scrimClickAction
        escapeKeyAction
        .heading=${dialogHeading("Create container")}
        @closed=${this.closeDialog}
      >
        <div class="content">
          <ha-textfield id="image" label="Image" dialogInitialFocus></ha-textfield>
          <ha-textfield id="name" label="Name"></ha-textfield>
          <ha-textfield id="network" label="Network"></ha-textfield>
          <ha-textfield id="ports" label="Ports (80:80, 443:443)"></ha-textfield>
          <ha-textfield id="volumes" label="Volumes (/host:/container)"></ha-textfield>
          <ha-select id="restart" label="Restart Policy">
            <mwc-list-item value="no">No</mwc-list-item>
            <mwc-list-item value="always">Always</mwc-list-item>
            <mwc-list-item value="on-failure">On Failure</mwc-list-item>
            <mwc-list-item value="unless-stopped">Unless Stopped</mwc-list-item>
          </ha-select>
        </div>
        <mwc-button slot="secondaryAction" @click=${this.closeDialog}>
          Cancel
        </mwc-button>
        <mwc-button
          slot="primaryAction"
          @click=${add}
        >
          Add
        </mwc-button>
      </ha-dialog>
    `;
  }

  static styles = [
    css`
      .content {
        display: flex;
        flex-direction: column;
        gap: 10px;
      }
      ha-textfield, ha-select {
        width: 100%;
      }
    `
  ];
}

function find_entities_by_model(devices, entities, model) {
  const device_id = devices.find((it) => it.model == model).id;
  return entities
    .filter((it) => it.device_id === device_id)
    .map((it) => ({ name: it.name || it.original_name, id: it.entity_id }));
}

class StrategyViewDockerContainers {
  static async generate(config, hass) {
    const [devices, entities] = await Promise.all([
      hass.callWS({ type: "config/device_registry/list" }),
      hass.callWS({ type: "config/entity_registry/list" }),
    ]);

    const devs = devices
      .filter((it) => it.model == "container")
      .map((it) => ({ name: it.name, id: it.identifiers[0][1] }));
    const volumes = find_entities_by_model(devices, entities, "volume");
    const images = find_entities_by_model(devices, entities, "image");
    return {
      sections: [
        {
          type: "grid",
          column_span: 4,
          cards: [
            {
              type: "custom:docker-title-card",
              heading: "Containers",
              actions: [
                {
                  type: 'button',
                  name: 'Create',
                  icon: 'mdi:plus',
                  action: { dialog: 'docker-create-container-dialog' }
                },
                {
                  type: 'button',
                  name: 'Prune Containers',
                  icon: 'mdi:delete-outline',
                  action: { event: 'press', entity_id: 'button.docker_host_prune_containers' }
                }
              ]
            },
            ...devs.map((it) => ({
              type: "custom:docker-container-card",
              container_id: it.id,
              name: it.name,
            })),
            {
              type: "custom:docker-title-card",
              heading: "Images",
              actions: [
                {
                  type: 'button',
                  name: 'Prune Images',
                  action: { event: 'press', entity_id: 'button.local_docker_images_prune_images' }
                }
              ]
            },
            ...images.map((it) => ({
              type: "custom:docker-image-card",
              entity_id: it.id,
              name: it.name,
            })),
            {
              type: "custom:docker-title-card",
              heading: "Volumes",
              actions: [
                {
                  type: 'button',
                  name: 'Prune Volumes',
                  action: { event: 'press', entity_id: 'button.local_docker_volumes_prune_volumes' }
                }
              ]
            },
            ...volumes.map((it) => ({
              type: "custom:docker-volume-card",
              entity_id: it.id,
              name: it.name,
            })),
          ],
        },
      ],
    };
  }
}

customElements.define("docker-container-card", DockerContainerCard);
customElements.define("docker-volume-card", DockerVolumeCard);
customElements.define("docker-title-card", DockerTitle);
customElements.define("docker-image-card", DockerImageCard);
customElements.define("docker-create-container-dialog", DockerAddDialog);
customElements.define(
  "ll-strategy-view-docker-containers",
  StrategyViewDockerContainers
);
