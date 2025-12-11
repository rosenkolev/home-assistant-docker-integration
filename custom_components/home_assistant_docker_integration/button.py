from homeassistant.components.button import DOMAIN as BUTTON_DOMAIN
from homeassistant.components.button import ButtonDeviceClass, ButtonEntity
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
    """Set up button platform."""

    coordinator = entry.runtime_data.data_coordinator

    auto_add_containers_devices(
        entry,
        async_add_entities,
        lambda id, _: DockerContainerRestartButton(coordinator, id),
    )


class DockerContainerRestartButton(BaseDeviceEntity[DockerContainerInfo], ButtonEntity):
    _attr_device_class = ButtonDeviceClass.RESTART
    _attr_entity_category = EntityCategory.CONFIG

    def __init__(
        self,
        coordinator: DockerDataUpdateCoordinator,
        device_id: str,
    ) -> None:
        dev = coordinator.data.containers.get(device_id)
        super().__init__(
            coordinator,
            device_id,
            name=dev.name,
            sub_name="restart",
        )

        self._init_entity_id(BUTTON_DOMAIN)
        self._attr_device_info = create_containers_device_info(dev, coordinator)
        self.id = dev.id

    async def async_press(self) -> None:
        await self.coordinator.api.async_container_restart(self.id)
