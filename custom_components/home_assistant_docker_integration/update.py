from homeassistant.components.update import DOMAIN as UPDATE_DOMAIN
from homeassistant.components.update import UpdateEntity, UpdateEntityFeature
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from ._api import DockerContainerInfo
from ._ha_helpers import DockerConfigEntry, auto_add_containers_devices
from .coordinator import DockerContainerVersionUpdateCoordinator
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

    def __init__(
        self,
        coordinator: DockerContainerVersionUpdateCoordinator,
        device: DockerContainerInfo,
    ) -> None:
        """Initialize the container power switch."""
        super().__init__(coordinator)

        self._attr_name = device.name + " update"
        self._attr_has_entity_name = True
        self._attr_unique_id = get_unique_id(device.short_id, "containers", "update")
        self._attr_device_info = create_containers_device_info(device, coordinator)

        self._container_id = device.id
        self._key = device.short_id

        self.coordinator = coordinator

    @property
    def available(self) -> bool:
        return self._key in self.coordinator.data and super().available

    @property
    def installed_version(self) -> str:
        return self.coordinator.data.get(self._key).current_ver

    @property
    def latest_version(self) -> str | None:
        return self.coordinator.data.get(self._key).new_ver

    async def async_install(self) -> None:
        await self.coordinator.api.async_container_update(self._container_id)
