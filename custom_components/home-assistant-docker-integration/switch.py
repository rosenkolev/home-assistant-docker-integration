from homeassistant.components.switch import DOMAIN as SWITCH_DOMAIN
from homeassistant.components.switch import SwitchEntity
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import EntityCategory
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from .const import COORDINATOR, DOMAIN
from .coordinator import DockerContainerInfo, DockerDataUpdateCoordinator
from .entity import BaseDeviceEntity


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up sensor platform."""

    coordinator: DockerDataUpdateCoordinator = hass.data[DOMAIN][COORDINATOR]

    @callback
    def _add_container_entities() -> None:
        """Add Entities."""
        if coordinator.tracker.added_containers:
            async_add_entities(
                DockerContainerSwitch(coordinator, device_id)
                for device_id in coordinator.tracker.added_containers
            )

    # listen for new containers
    _add_container_entities()
    entry.async_on_unload(coordinator.async_add_listener(_add_container_entities))


class DockerContainerSwitch(BaseDeviceEntity[DockerContainerInfo], SwitchEntity):
    _attr_entity_category = EntityCategory.CONFIG

    def __init__(
        self,
        coordinator: DockerDataUpdateCoordinator,
        device_id: str,
    ) -> None:
        """Initialize the container power switch."""
        dev = coordinator.data.containers.get(device_id)
        super().__init__(
            coordinator,
            device_id,
            key="containers",
            name=dev.name,
        )

        self._init_entity_id(SWITCH_DOMAIN)
        self._init_device_info(device_id, dev.name, dev.image_name)

    @property
    def is_on(self) -> bool | None:
        """Return true if device is on."""
        return self.device.state == "running"

    async def async_turn_on(self, **kwargs) -> None:
        """Turn the device on."""
        if not self.is_on:
            await self.coordinator.api.async_container_start(self._id)

    async def async_turn_off(self, **kwargs) -> None:
        """Turn the device off."""
        if self.is_on:
            await self.coordinator.api.async_container_stop(self._id)
