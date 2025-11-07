class StrategyViewDockerContainers {
  static async generate(config, hass) {
    return {
      "cards": [
        {
          "type": "markdown",
          "content": `Generated at ${(new Date).toLocaleString()}`
        }
      ]
    };
  }
}

customElements.define("ll-strategy-view-docker-containers", StrategyViewDockerContainers);