class StrategyViewDockerContainers {
  static async generate(config, hass) {
    const [devices, entities] = await Promise.all([
      hass.callWS({ type: "config/device_registry/list" }),
      hass.callWS({ type: "config/entity_registry/list" }),
    ]);
    const containers = entities.filter(it => it.entity_id.startsWith("sensor.docker_container_"))
    if (containers) { console.log(containers[0]) }
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
            ...containers.map(it => ({
              type: "tile",
              entity: it.entity_id,
              vertical: false,
              grid_options: {
                columns: "full"
              }
            }))
          ]
        }
      ]
    };
  }
}

customElements.define("ll-strategy-view-docker-containers", StrategyViewDockerContainers);