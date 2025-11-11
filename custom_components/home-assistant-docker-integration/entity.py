from homeassistant.const import EntityCategory
from homeassistant.helpers.device_registry import DeviceInfo
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .const import DOMAIN
from .coordinator import DOCKER_DATA_KEYS, DockerDataUpdateCoordinator


def to_suffix(suffix: str, lead_char=" ") -> str:
    return "" if suffix is None else (lead_char + suffix)


class BaseDeviceEntity[TDevice](CoordinatorEntity[DockerDataUpdateCoordinator]):
    _attr_entity_category = EntityCategory.DIAGNOSTIC

    def __init__(
        self,
        coordinator: DockerDataUpdateCoordinator,
        id: str,
        key: DOCKER_DATA_KEYS,
        name: str,
        sub_name: str = None,
    ):
        super().__init__(coordinator)
        self._id = id
        self._key = key
        self._attr_name = name + to_suffix(sub_name)
        self._attr_has_entity_name = True
        self._attr_unique_id = f"{DOMAIN}_{key}_{id}{to_suffix(sub_name, '_')}"

    def _init_entity_id(self, entity_domain: str):
        self.entity_id = f"{entity_domain}.{self._attr_unique_id}"

    def _init_device_info(
        self,
        device_id: str,
        device_name: str,
        model_id: str = None,
        device_serial: str = None,
    ):
        self._attr_device_info = DeviceInfo(
            identifiers={(DOMAIN, device_id)},
            model=self._key,
            model_id=model_id,
            name=device_name,
            serial_number=device_serial,
            via_device=(DOMAIN, self.coordinator.config_entry.entry_id),
        )

    @property
    def _dataset(self) -> dict[str, TDevice]:
        return getattr(self.coordinator.data, self._key)

    @property
    def available(self) -> bool:
        """Return if entity is available."""
        return self._id in self._dataset and super().available

    @property
    def device(self) -> TDevice:
        """Return data for this device."""
        return self._dataset.get(self._id)


def create_volumes_device_info(coordinator: DockerDataUpdateCoordinator) -> DeviceInfo:
    return DeviceInfo(
        identifiers={(DOMAIN, "docker_integration_volumes")},
        model="volume",
        name="Local Docker Volumes",
        via_device=(DOMAIN, coordinator.config_entry.entry_id),
    )


def create_images_device_info(coordinator: DockerDataUpdateCoordinator) -> DeviceInfo:
    return DeviceInfo(
        identifiers={(DOMAIN, "docker_integration_images")},
        model="image",
        name="Local Docker Images",
        via_device=(DOMAIN, coordinator.config_entry.entry_id),
    )
