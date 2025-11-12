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

class BaseRowLitElement extends LitElement {
  static get properties() {
    return {
      hass: { attribute: false },
      stateObj: { attribute: false },
      config: { state: true },
    };
  }

  /** @type {HomeAssistant} */ hass;
  /** @type {HassEntity} */ stateObj;
  /** @type {string[]} */ tracked_state_keys;

  _assert_config(config, config_keys) {
    if (config_keys) {
      if (!config) throw new Error("No configurations found");
      for (const key of config_keys) {
        if (!config[key]) throw new Error("No configuration " + key);
      }
    }
  }

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
      background-color: #fef0f0;
    }
  `;
}

class DockerContainerCard extends BaseRowLitElement {
  /** @type {{ container_id: string, name: string }} */
  config;

  setConfig(config) {
    this._assert_config(config, ["container_id", "name"]);
    this._key_switch = `switch.docker_integration_containers_${config.container_id}`;
    this._key_sensor = `sensor.docker_integration_containers_${config.container_id}`;
    this.tracked_state_keys = [this._key_switch, this._key_sensor];
    this.config = config;
  }

  render_body() {
    const sensor_state = this.hass.states[this._key_sensor];
    const switch_state = this.hass.states[this._key_switch];
    const isOn = switch_state.state === "on";
    const toggle = () =>
      this.hass.callService("homeassistant", "toggle", {
        entity_id: this._key_switch,
      });
    const restart = () =>
      this.hass.callService("homeassistant", "toggle", {
        entity_id: this._key_switch,
      });
    const _handleAction = (test, n) => {
      console.log(test, n);
    };
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
            ${sensor_state.attributes.ports.map(
              (port) => html`
                <ha-assist-chip class="port" .label="${port}"></ha-assist-chip>
              `
            )}
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
    this._assert_config(config, ["entity_id", "name"]);
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
    this._assert_config(config, ["entity_id", "name"]);
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
      }
    `,
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
      .filter((it) => it.model == "containers")
      .map((it) => ({ name: it.name, id: it.identifiers[0][1] }));
    const volumes = find_entities_by_model(devices, entities, "volumes");
    const images = find_entities_by_model(devices, entities, "images");
    return {
      sections: [
        {
          type: "grid",
          column_span: 4,
          cards: [
            {
              type: "heading",
              heading: "Containers",
            },
            ...devs.map((it) => ({
              type: "custom:docker-container-card",
              container_id: it.id,
              name: it.name,
            })),
            {
              type: "heading",
              heading: "Images",
            },
            ...images.map((it) => ({
              type: "custom:docker-image-card",
              entity_id: it.id,
              name: it.name,
            })),
            {
              type: "heading",
              heading: "Volumes",
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
customElements.define("docker-image-card", DockerImageCard);
customElements.define(
  "ll-strategy-view-docker-containers",
  StrategyViewDockerContainers
);

/* <ha-button-menu
                @action=${_handleAction}
                @closed=${(ev) => ev.stopPropagation()}
              >
              <ha-icon-button slot="trigger" .label="action">
                <ha-icon .icon="mdi:dots-vertical"></ha-icon>
              </ha-icon-button>
              <ha-list-item graphic="icon">Restart</ha-list-item>
              <ha-list-item graphic="icon">Remove</ha-list-item>
            </ha-button-menu>
            */