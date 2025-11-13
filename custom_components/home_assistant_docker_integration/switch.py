from homeassistant.components.switch import DOMAIN as SWITCH_DOMAIN
from homeassistant.components.switch import SwitchEntity
from homeassistant.const import EntityCategory
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from ._docker_api import DockerContainerInfo
from .coordinator import (
    DockerConfigEntry,
    DockerDataUpdateCoordinator,
    auto_add_containers_devices,
)
from .entity import BaseDeviceEntity, create_containers_device_info


async def async_setup_entry(
    hass: HomeAssistant,
    entry: DockerConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up switch platform."""
    auto_add_containers_devices(
        entry,
        async_add_entities,
        lambda device_id, coordinator: DockerContainerSwitch(coordinator, device_id),
    )


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
            name=dev.name,
        )

        self._init_entity_id(SWITCH_DOMAIN)
        self._attr_device_info = create_containers_device_info(dev, coordinator)
        self.id = dev.id

    @property
    def is_on(self) -> bool | None:
        """Return true if device is on."""
        return self.device.state == "running"

    async def async_turn_on(self, **kwargs) -> None:
        """Turn the device on."""
        if not self.is_on:
            await self.coordinator.api.async_container_start(self.id)

    async def async_turn_off(self, **kwargs) -> None:
        """Turn the device off."""
        if self.is_on:
            await self.coordinator.api.async_container_stop(self.id)
