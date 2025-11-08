from homeassistant.components.switch import (
    DOMAIN as SWITCH_DOMAIN,
)
from homeassistant.components.switch import SwitchEntity
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from .const import COORDINATOR, DOMAIN
from .coordinator import DockerDataUpdateCoordinator
from .entity import ContainerEntity


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up sensor platform."""

    coordinator = hass.data[DOMAIN][COORDINATOR]

    @callback
    def _add_container_entities() -> None:
        """Add Entities."""
        if coordinator.new_device_ids:
            async_add_entities(
                DockerContainerSwitch(coordinator, device_id)
                for device_id in coordinator.new_device_ids
            )

    _add_container_entities()

    # listen for new containers
    entry.async_on_unload(coordinator.async_add_listener(_add_container_entities))


class DockerContainerSwitch(ContainerEntity, SwitchEntity):
    def __init__(
        self, coordinator: DockerDataUpdateCoordinator, device_id: str
    ) -> None:
        """Initialize the container power switch."""
        super().__init__(coordinator, device_id)
        self._attr_name = self.device.name
        self.entity_id = f"{SWITCH_DOMAIN}.{self._attr_unique_id}"
        # self._attr_should_poll = False

    @property
    def is_on(self) -> bool | None:
        """Return true if device is on."""
        return self.device.status == "running"

    async def async_turn_on(self, **kwargs) -> None:
        """Turn the device on."""
        if not self.is_on:
            await self.coordinator.api.async_container_start(self._dev_id)

    async def async_turn_off(self, **kwargs) -> None:
        """Turn the device off."""
        if self.is_on:
            await self.coordinator.api.async_container_stop(self._dev_id)
