from typing import Any
from homeassistant.config_entries import ConfigEntry
from homeassistant.helpers.entity import Entity
from homeassistant.core import HomeAssistant, callback

type DockerHostConfigEntry = ConfigEntry[DockerHost]

STATE_ATTR_CPU = "cpu_usage"
STATE_ATTR_MEMORY = "memory_usage"

class DockerHost(Entity):
    _unrecorded_attributes = frozenset(
        {
            STATE_ATTR_CPU,
            STATE_ATTR_MEMORY,
        }
    )

    def __init__(self, hass: HomeAssistant) -> None:
        self._attr_name = "Docker Host"
        self._attr_unique_id = "docker_integration_docker_host"
        self._attr_has_entity_name = True

    @property
    def state(self) -> str:
        return "v12.0"

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        """Return the state attributes."""
        return {
            STATE_ATTR_CPU: "5%",
            STATE_ATTR_MEMORY: "13%",
        }

    @callback
    def remove_listeners(self) -> None:
        """Remove listeners."""
