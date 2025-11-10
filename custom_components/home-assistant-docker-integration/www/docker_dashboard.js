import {
  LitElement,
  html,
  css,
  nothing
} from "https://unpkg.com/lit-element@3.3.3/lit-element.js?module";

import {
  hasConfigOrEntityChanged
} from 'https://unpkg.com/custom-card-helpers@1.9.0/dist/index.m.js?module';

class DockerRowBaseCard extends LitElement {
  static get properties() {
    return {
      hass: { attribute: false },
      config: { state: true }
    };
  }

  setConfig(config, validate_props) {
    if (!config) throw new Error("No configurations found");
    for (const key of validate_props) {
      if (!config[key]) throw new Error("You need to define a " + key);
    }

    this.config = config;
  }

  shouldUpdate(changedProps) {
    return this.config && this.hass && hasConfigOrEntityChanged(this, changedProps, false);
  }

  render() {
    if (!this.config || !this.hass) {
      return nothing;
    }

    return this.render_body();
  }

  getCardSize() { return 4; }
  getGridOptions() { return { columns: "full" }; }
}

function register_hass_component(name, id, cls) {
  customElements.define(id, cls);

  window.customCards = window.customCards || [];
  window.customCards.push({
    type: id,
    name: name,
    description: "A custom docker component!",
    //documentationURL: "https://developers.home-assistant.io/docs/frontend/custom-ui/custom-card/",
  });
}

class DockerContainerCard extends DockerRowBaseCard {
  setConfig(config) {
    super.setConfig(config, ['container_id', 'name'])
  }

  render_body() {
    const state_id = `sensor.docker_integration_containers_${this.config.container_id}`;
    const switch_id = `switch.docker_integration_containers_${this.config.container_id}`;
    const sensor_state = this.hass.states[state_id];
    const switch_state = this.hass.states[switch_id];
    const isOn = switch_state.state === "on"
    const toggle = () => this.hass.callService('homeassistant', 'toggle', { entity_id: switch_id })
    return html`
      <ha-card>
        <div class="content">
          <div class="status">
            <span class="label">${this.config.name}</span>
            <span class="state">${sensor_state.state}</span>
            <span class="status">${sensor_state.attributes.status}</span>
          </div>
          <div class="control">
            <ha-switch .checked="${isOn}" @click=${() => toggle()}></ha-switch>
          </div>
        </div>
      </ha-card>
      `
  }

  // styles
  static get styles() {
    return css`
      .content {
        display: flex;
        padding: 5px 10px;
        justify-content: space-between;
       }
    `;
  }
}

class DockerVolumeCard extends DockerRowBaseCard {
  setConfig(config) {
    super.setConfig(config, ['entity_id', 'name'])
  }

  render_body() {
    const state = this.hass.states[entity_id];
    return html`
      <ha-card>
        <div class="content">
          <div class="status">
            <span class="label">${this.config.name}</span>
            <span class="state">${state.state}</span>
            <span class="size">${state.attributes.size}</span>
          </div>
          <div class="control">
          </div>
        </div>
      </ha-card>
      `
  }

  // styles
  static get styles() {
    return css`
      .content {
        display: flex;
        padding: 5px 10px;
        justify-content: space-between;
       }
    `;
  }
}


class StrategyViewDockerContainers {
  static async generate(config, hass) {
    const [devices, entities] = await Promise.all([
      hass.callWS({ type: 'config/device_registry/list' }),
      hass.callWS({ type: 'config/entity_registry/list' }),
    ]);

    const devs = devices.filter(it => it.model == "containers").map(it => ({ name: it.name, id: it.identifiers[0][1] }));
    const volume_device_id = devices.find(it => it.model == "volumes").id;
    const volumes = entities.filter(it => it.device_id === volume_device_id).map(it => ({ name: it.name || it.original_name, id: it.entity_id }));
    return {
      "sections": [
        {
          type: "grid",
          column_span: 4,
          cards: [
            {
              type: "heading",
              heading: "Containers"
            },
            ...devs.map(it => ({
              type: "custom:docker-container-card",
              container_id: it.id,
              name: it.name
            })),
            {
              type: "heading",
              heading: "Images"
            },
            {
              type: "heading",
              heading: "Volumes"
            },
            ...volumes.map(it => ({
              type: "custom:docker-volume-card",
              entity_id: it.id,
              name: it.name
            })),
          ]
        }
      ]
    };
  }
}

register_hass_component('Docker Container Card', 'docker-container-card', DockerContainerCard);
register_hass_component('Docker Volume Card', 'docker-volume-card', DockerVolumeCard);
customElements.define("ll-strategy-view-docker-containers", StrategyViewDockerContainers);