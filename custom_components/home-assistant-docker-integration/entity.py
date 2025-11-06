from homeassistant.helpers.device_registry import DeviceInfo
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .const import DOMAIN
from .coordinator import DockerContainerInfo, DockerDataUpdateCoordinator


class ContainerEntity(CoordinatorEntity[DockerDataUpdateCoordinator]):
    _attr_has_entity_name = True

    def __init__(
        self,
        coordinator: DockerDataUpdateCoordinator,
        device_id: str,
    ) -> None:
        """Initialise the entity."""
        super().__init__(coordinator)
        self._dev_id = device_id

        dev = self.device
        self._attr_device_info = DeviceInfo(
            identifiers={(DOMAIN, device_id)},
            model=dev.image_name,
            # model_id=dev.image_id,
            name=dev.name,
            via_device=(DOMAIN, coordinator.config_entry.entry_id),
        )

    @property
    def available(self) -> bool:
        """Return if entity is available."""
        return self._dev_id in self.coordinator.data.containers and super().available

    @property
    def device(self) -> DockerContainerInfo:
        """Return data for this device."""
        return self.coordinator.data.containers[self._dev_id]
