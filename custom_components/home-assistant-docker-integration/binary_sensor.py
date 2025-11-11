from homeassistant.components.binary_sensor import DOMAIN as BINARY_SENSOR_DOMAIN
from homeassistant.components.binary_sensor import BinarySensorEntity
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback

from .const import COORDINATOR, DOMAIN
from .coordinator import DockerDataUpdateCoordinator, DockerImageInfo, DockerVolumeInfo
from .entity import (
    BaseDeviceEntity,
    create_images_device_info,
    create_volumes_device_info,
)


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddConfigEntryEntitiesCallback,
) -> None:
    """Set up binary sensor platform."""

    coordinator: DockerDataUpdateCoordinator = hass.data[DOMAIN][COORDINATOR]

    @callback
    def _add_container_entities() -> None:
        """Add Entities."""
        if coordinator.tracker.added_images:
            async_add_entities(
                DockerImageSensor(coordinator, image_id)
                for image_id in coordinator.tracker.added_images
            )

        if coordinator.tracker.added_volumes:
            async_add_entities(
                DockerVolumeSensor(coordinator, volume_id)
                for volume_id in coordinator.tracker.added_volumes
            )

    _add_container_entities()
    entry.async_on_unload(coordinator.async_add_listener(_add_container_entities))


class DockerImageSensor(BaseDeviceEntity[DockerImageInfo], BinarySensorEntity):
    def __init__(
        self,
        coordinator: DockerDataUpdateCoordinator,
        data_id: str,
    ) -> None:
        dev = coordinator.data.images.get(data_id)
        super().__init__(
            coordinator,
            data_id,
            key="images",
            name=dev.tag or dev.title or dev.rev or dev.id,
        )

        self._init_entity_id(BINARY_SENSOR_DOMAIN)
        self._attr_device_info = create_images_device_info(coordinator)

    @property
    def is_on(self) -> bool:
        return self.device.in_use

    @property
    def extra_state_attributes(self) -> dict[str, str]:
        dev = self.device
        return {
            "tag": dev.tag,
            "title": dev.title,
            "revision": dev.rev,
            "description": dev.description,
        }


class DockerVolumeSensor(BaseDeviceEntity[DockerVolumeInfo], BinarySensorEntity):
    def __init__(
        self,
        coordinator: DockerDataUpdateCoordinator,
        data_id: str,
    ) -> None:
        dev = coordinator.data.volumes.get(data_id)
        super().__init__(
            coordinator,
            data_id,
            key="volumes",
            name=dev.name,
        )

        self._init_entity_id(BINARY_SENSOR_DOMAIN)
        self._attr_device_info = create_volumes_device_info(coordinator)

    @property
    def is_on(self) -> bool:
        return self.device.in_use

    @property
    def extra_state_attributes(self) -> dict[str, str]:
        dev = self.device
        return {"size": dev.size, "mount": dev.mount_point}
