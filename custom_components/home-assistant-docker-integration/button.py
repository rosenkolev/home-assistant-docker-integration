from homeassistant.components.button import (
    DOMAIN as BUTTON_DOMAIN,
)
from homeassistant.components.button import ButtonDeviceClass, ButtonEntity
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import EntityCategory
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
    """Set up button platform."""

    coordinator = hass.data[DOMAIN][COORDINATOR]

    @callback
    def _add_container_entities() -> None:
        """Add Entities."""
        if coordinator.new_device_ids:
            async_add_entities(
                ContainerRestartButton(coordinator, device_id)
                for device_id in coordinator.new_device_ids
            )

    _add_container_entities()

    # listen for new containers
    entry.async_on_unload(coordinator.async_add_listener(_add_container_entities))


class ContainerRestartButton(ContainerEntity, ButtonEntity):
    _attr_device_class = ButtonDeviceClass.RESTART
    _attr_entity_category = EntityCategory.CONFIG

    def __init__(
        self, coordinator: DockerDataUpdateCoordinator, device_id: str
    ) -> None:
        """Initialize the container."""
        super().__init__(coordinator, device_id, "restart")
        self._attr_name = self.device.name
        self.entity_id = f"{BUTTON_DOMAIN}.{self._attr_unique_id}"

    async def async_press(self) -> None:
        """Handle the button press."""
        await self.coordinator.api.async_container_restart(self._dev_id)
