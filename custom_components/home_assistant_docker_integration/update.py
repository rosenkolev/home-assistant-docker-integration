from homeassistant.components.update import DOMAIN as UPDATE_DOMAIN
from homeassistant.components.update import (
    UpdateDeviceClass,
    UpdateEntity,
    UpdateEntityFeature,
)
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from ._docker_api import DockerContainerInfo
from .coordinator import (
    DockerConfigEntry,
    DockerContainerVersionUpdateCoordinator,
    auto_add_containers_devices,
)
from .entity import create_containers_device_info, get_unique_id


async def async_setup_entry(
    hass: HomeAssistant,
    entry: DockerConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up update platform."""

    update_coordinator = entry.runtime_data.update_coordinator

    auto_add_containers_devices(
        entry,
        async_add_entities,
        lambda id, coordinator: DockerContainerUpdate(
            update_coordinator, coordinator.data.containers.get(id)
        ),
    )


class DockerContainerUpdate(
    CoordinatorEntity[DockerContainerVersionUpdateCoordinator], UpdateEntity
):
    _attr_supported_features = UpdateEntityFeature.INSTALL
    _attr_device_class = UpdateDeviceClass.FIRMWARE
    _attr_has_entity_name = True
    _attr_icon = "mdi:docker"

    def __init__(
        self,
        coordinator: DockerContainerVersionUpdateCoordinator,
        device: DockerContainerInfo,
    ) -> None:
        """Initialize the container power switch."""
        super().__init__(coordinator)

        self._attr_name = "container update"
        self._attr_title = "New container " + device.name
        self._attr_unique_id = get_unique_id(device.short_id, "containers", "update")
        self._attr_device_info = create_containers_device_info(device, coordinator)

        self._container_id = device.id
        self._key = device.image_name

        self.coordinator = coordinator
        self.entity_id = f"{UPDATE_DOMAIN}.{self._attr_unique_id}"

    @property
    def available(self) -> bool:
        return self._key in self.coordinator.data and super().available

    @property
    def installed_version(self) -> str:
        return self.coordinator.data.get(self._key).current_ver

    @property
    def latest_version(self) -> str | None:
        return self.coordinator.data.get(self._key).new_ver

    async def async_install(self, version: str | None, backup: bool, **kwargs) -> None:
        await self.coordinator.api.async_container_update(self._container_id)
