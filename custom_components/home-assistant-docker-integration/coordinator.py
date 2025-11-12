import typing
from datetime import timedelta

from homeassistant.core import HomeAssistant
from homeassistant.helpers import device_registry as dr
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator

from . import DockerConfigEntry
from ._api import DockerApi, DockerHostInfo, DockerImageUpdateInfo
from .const import _LOGGER, DOMAIN

SCAN_INTERVAL = timedelta(seconds=5)

DOCKER_DATA_KEYS = typing.Literal["containers", "images", "volumes"]


class ServiceController:
    def __init__(self, hass: HomeAssistant, entry: DockerConfigEntry):
        self.api = DockerApi()
        self.data_coordinator = DockerDataUpdateCoordinator(hass, entry)
        self.update_coordinator = DockerContainerVersionUpdateCoordinator(hass, entry)
        self.name = "Docker Host"

    @property()
    def version(self) -> str:
        return self._data_coordinator.data.version

    async def async_initialize(self):
        await self.api.async_connect()
        await self.data_coordinator.async_config_entry_first_refresh()

    async def async_shutdown(self):
        await self.data_coordinator.async_shutdown()
        await self.update_coordinator.async_shutdown()
        self.api.disconnect()

        # clean up because: self.data_coordinator.config_entry.runtime_data == self
        self.data_coordinator = None
        self.update_coordinator = None


class DockerDataUpdateCoordinator(DataUpdateCoordinator[DockerHostInfo]):
    """Data update coordinator for integration"""

    def __init__(self, hass: HomeAssistant, config_entry: DockerConfigEntry) -> None:
        super().__init__(
            hass,
            _LOGGER,
            config_entry=config_entry,
            name=DOMAIN,
            update_interval=SCAN_INTERVAL,
        )

        self.tracker = DeviceTracker(config_entry.entry_id)
        self.data: DockerHostInfo = {}

    @property
    def api(self) -> DockerApi:
        return self.config_entry.runtime_data.api

    async def _async_update_data(self) -> DockerHostInfo:
        self.tracker.reset_added_devices()

        data = await self.api.async_fetch_data()

        self.tracker.set_device_ids(
            set(data.containers.keys()),
            set(data.volumes.keys()),
            set(data.images.keys()),
        )

        return data


class DockerContainerVersionUpdateCoordinator(DataUpdateCoordinator):
    """Check for docker container/image update"""

    def __init__(self, hass: HomeAssistant, config_entry: DockerConfigEntry) -> None:
        super().__init__(
            hass,
            _LOGGER,
            config_entry=config_entry,
            name="docker_integration_container_versions",
            update_interval=timedelta(hours=6),
        )
        self.data: dict[str, DockerImageUpdateInfo] = {}

    @property
    def api(self) -> DockerApi:
        return self.config_entry.runtime_data.api

    async def _async_update_data(self) -> dict[str, DockerImageUpdateInfo]:
        """Fetch data."""
        api: DockerApi = self.config_entry.config_entry.api
        data: DockerHostInfo = self.config_entry.runtime_data.data_coordinator.data
        image_names = set(
            [c.image_name for c in data.containers.values() if c.state == "running"]
        )
        images = dict(
            (image, await api.async_images_check_update(image)) for image in image_names
        )

        return {
            key: images.get(c.image_name)
            for key, c in data.containers.items()
            if c.image_name in images
        }


class DeviceTracker:
    _current_device_ids = set[str]()
    _removed_device_ids = set[str]()

    added_containers = set[str]()
    added_volumes = set[str]()
    added_images = set[str]()

    def __init__(self, service_id: str):
        self.base_id = service_id
        self.service_device_id: str | None = None

    def reset_added_devices(self):
        self.added_containers.clear()
        self.added_volumes.clear()
        self.added_images.clear()

    def set_device_ids(
        self, container_ids: set[str], volume_ids: set[str], image_ids: set[str]
    ):
        all_ids = set(container_ids | volume_ids | image_ids)
        removed_device_ids = self._current_device_ids - all_ids

        self.added_containers = container_ids - self._current_device_ids
        self.added_volumes = volume_ids - self._current_device_ids
        self.added_images = image_ids - self._current_device_ids
        self._current_device_ids = all_ids

        # Clean registries when removed devices found.
        if len(removed_device_ids) > 0:
            # get all devices in the service
            device_reg = dr.async_get(self.hass)
            device_list = dr.async_entries_for_config_entry(device_reg, self.base_id)

            # Find the container entities
            if self.service_device_id is None:
                gateway_device = device_reg.async_get_device({(DOMAIN, self.base_id)})
                assert gateway_device is not None
                self.service_device_id = gateway_device.id

            # Then remove the connected orphaned device(s)
            for device_entry in device_list:
                for domain_name, entry_id in enumerate(device_entry.identifiers):
                    if (
                        domain_name == DOMAIN
                        and device_entry.via_device_id == self.service_device_id
                        and entry_id not in all_ids
                    ):
                        device_reg.async_update_device(
                            device_entry.id,
                            remove_config_entry_id=self.config_entry.entry_id,
                        )
                        _LOGGER.debug(
                            "Removed %s device %s %s from device_registry",
                            DOMAIN,
                            device_entry.model,
                            entry_id,
                        )
