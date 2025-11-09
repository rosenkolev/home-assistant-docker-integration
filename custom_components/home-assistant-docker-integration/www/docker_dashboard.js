import {
  LitElement,
  html,
  css,
  nothing
} from "https://unpkg.com/lit-element@3.3.3/lit-element.js?module";

import {
  hasConfigOrEntityChanged
} from 'https://unpkg.com/custom-card-helpers@1.9.0/dist/index.m.js?module';

class DockerContainerCard extends LitElement {
  static get properties() {
    return {
      hass: { attribute: false },
      config: { state: true }
    };
  }

  setConfig(config) {
    if (!config || !config.container_id) {
      throw new Error("You need to define a container_id");
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

    const health_id = `sensor.docker_integration_container_${this.config.container_id}_health`;
    const switch_id = `switch.docker_integration_container_${this.config.container_id}`;
    const sensor_health_state = this.hass.states[health_id];
    const switch_state = this.hass.states[switch_id];
    const isOn = switch_state.state === "on"
    const toggle = () => this.hass.callService('homeassistant', 'toggle', { entity_id: switch_id })
     return html`
      <ha-card>
        <div class="content">
          <div class="status">
            <span class="label">${this.config.name}</span>
            <span class="value">${sensor_health_state.state} ${sensor_health_state.attributes.unit_of_measurement || ""}</span>
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

  getCardSize() {
    return 4;
  }

  getGridOptions() {
    return {
      columns: "full"
    };
  }
}

customElements.define("docker-container-card", DockerContainerCard);

window.customCards = window.customCards || [];
window.customCards.push({
  type: "docker-container-card",
  name: "Docker Container Card",
  description: "A docker container card!",
  //documentationURL: "https://developers.home-assistant.io/docs/frontend/custom-ui/custom-card/",
});

class StrategyViewDockerContainers {
  static async generate(config, hass) {
    const _devices = await hass.callWS({ type: "config/device_registry/list" });
    const devs = _devices.filter(it => it.model=="container").map(it => ({ name: it.name, id: it.identifiers[0][1] }));
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
            }))
          ]
        }
      ]
    };
  }
}

customElements.define("ll-strategy-view-docker-containers", StrategyViewDockerContainers);