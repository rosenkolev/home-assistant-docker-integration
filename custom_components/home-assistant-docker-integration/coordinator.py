import asyncio
from dataclasses import dataclass
from datetime import timedelta

import docker
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers import device_registry as dr
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator

from .const import _LOGGER, DOMAIN

SCAN_INTERVAL = timedelta(seconds=5)


@dataclass(kw_only=True)
class DockerContainerInfo:
    id: str
    name: str
    status: str
    image_id: str
    image_name: str
    short_id: str
    ports: list[str]


@dataclass(kw_only=True)
class DockerHostInfo:
    version: str
    containers_total: int
    containers_running: int
    images: int
    firewall: str
    containers: dict[str, DockerContainerInfo]


class DockerApi:
    def __init__(self):
        self.base_url = "unix://var/run/docker.sock"
        self.loop = asyncio.get_running_loop()
        self.client = None

    @property
    def connected(self) -> bool:
        return self.client is not None and isinstance(self.client, docker.DockerClient)

    async def async_connect(self) -> None:
        def docker_client_init(obj):
            obj.client = docker.DockerClient(base_url=obj.base_url)

        await self.loop.run_in_executor(None, docker_client_init, self)

    async def async_fetch_data(self):
        def docker_data(client):
            info = client.info()
            containers = client.containers.list(all=True)
            return DockerHostInfo(
                version=info["ServerVersion"],
                firewall=info["FirewallBackend"]["Driver"],
                containers_total=info["Containers"],
                containers_running=info["ContainersRunning"],
                images=info["Images"],
                containers=dict(
                    map(
                        lambda x: (
                            x.short_id,
                            DockerContainerInfo(
                                id=x.id,
                                name=x.name,
                                status=x.status,
                                short_id=x.short_id,
                                image_id=x.attrs["Image"],
                                image_name=x.attrs["Config"]["Image"],
                                ports=["xxx"],
                            ),
                        ),
                        containers,
                    )
                ),
            )

        return await self.loop.run_in_executor(None, docker_data, self.client)

    def disconnect(self):
        if self.connected:
            self.client.close()
            self.client = None


class DockerDataUpdateCoordinator(DataUpdateCoordinator[DockerHostInfo]):
    """Data update coordinator for integration"""

    def __init__(self, hass: HomeAssistant, config_entry: ConfigEntry) -> None:
        super().__init__(
            hass,
            _LOGGER,
            config_entry=config_entry,
            name=DOMAIN,
            update_interval=SCAN_INTERVAL,
        )

        self._api = DockerApi()
        self._containers: set[str] = set()
        self.new_containers: set[str] = set()

    async def _async_update_data(self) -> DockerHostInfo:
        self.new_containers.clear()

        if not self._api.connected:
            await self._api.async_connect()

        data = await self._api.async_fetch_data()
        self._async_add_remove_containers(data.containers)

        return data

    def _async_add_remove_containers(
        self, containers: dict[str, DockerContainerInfo]
    ) -> None:
        container_names = set(containers.keys())
        self.new_containers = container_names - self._containers

        if len(self._containers - container_names):
            # Remove containers that don't exists
            self._async_remove_devices(container_names)

        # update current
        self._containers = container_names

    def _async_remove_devices(self, data: set[str]) -> None:
        """Clean registries when removed devices found."""
        service_id = self.config_entry.entry_id
        device_reg = dr.async_get(self.hass)
        device_list = dr.async_entries_for_config_entry(device_reg, service_id)

        # Find the container entities
        gateway_device = device_reg.async_get_device({(DOMAIN, service_id)})
        assert gateway_device is not None
        via_device_id = gateway_device.id

        # Then remove the connected orphaned device(s)
        for device_entry in device_list:
            for domain_name, entry_id in enumerate(device_entry.identifiers):
                if (
                    domain_name == DOMAIN
                    and device_entry.via_device_id == via_device_id
                    and entry_id not in data
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

    async def async_disconnect(self):
        self._api.disconnect()
        await self.async_shutdown()
