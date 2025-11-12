from homeassistant.components.button import DOMAIN as BUTTON_DOMAIN
from homeassistant.components.button import ButtonDeviceClass, ButtonEntity
from homeassistant.const import EntityCategory
from homeassistant.core import HomeAssistant
from homeassistant.helpers.device_registry import DeviceInfo
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from ._ha_helpers import DockerConfigEntry, auto_add_containers_devices
from .const import DOMAIN
from .coordinator import DockerContainerInfo, DockerDataUpdateCoordinator
from .entity import (
    BaseDeviceEntity,
    create_containers_device_info,
    create_images_device_info,
    create_volumes_device_info,
)


async def async_setup_entry(
    hass: HomeAssistant,
    entry: DockerConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up button platform."""

    coordinator = entry.runtime_data.data_coordinator

    async_add_entities(
        [
            DockerVolumePruneButton(coordinator),
            DockerImagesPruneButton(coordinator),
            DockerContainersPruneButton(coordinator),
        ]
    )

    auto_add_containers_devices(
        entry, async_add_entities, lambda id, _: ContainerRestartButton(coordinator, id)
    )


class ContainerRestartButton(BaseDeviceEntity[DockerContainerInfo], ButtonEntity):
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

    async def async_press(self) -> None:
        await self.coordinator.api.async_container_restart(self._id)


class DockerVolumePruneButton(ButtonEntity):
    _attr_entity_category = EntityCategory.CONFIG
    _attr_name = "Prune Volumes"
    _attr_has_entity_name = True
    _attr_unique_id = "volumes_prune"

    def __init__(self, coordinator: DockerDataUpdateCoordinator) -> None:
        self._attr_device_info = create_volumes_device_info(coordinator)
        self.coordinator = coordinator

    async def async_press(self) -> None:
        await self.coordinator.api.async_volumes_prune()


class DockerImagesPruneButton(ButtonEntity):
    _attr_entity_category = EntityCategory.CONFIG
    _attr_name = "Prune Images"
    _attr_has_entity_name = True
    _attr_unique_id = "images_prune"

    def __init__(self, coordinator: DockerDataUpdateCoordinator) -> None:
        self._attr_device_info = create_images_device_info(coordinator)
        self.coordinator = coordinator

    async def async_press(self) -> None:
        await self.coordinator.api.async_images_prune()


class DockerContainersPruneButton(ButtonEntity):
    _attr_entity_category = EntityCategory.CONFIG
    _attr_name = "Prune Containers"
    _attr_has_entity_name = True
    _attr_unique_id = "containers_prune"

    def __init__(self, coordinator: DockerDataUpdateCoordinator) -> None:
        self._attr_device_info = DeviceInfo(
            identifiers={(DOMAIN, coordinator.config_entry.entry_id)}
        )
        self.coordinator = coordinator

    async def async_press(self) -> None:
        await self.coordinator.api.async_containers_prune()
