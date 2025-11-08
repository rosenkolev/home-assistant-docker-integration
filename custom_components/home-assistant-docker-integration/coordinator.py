import asyncio
from dataclasses import dataclass
from datetime import timedelta

import docker
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator

from ._mixins import AutoDiscoverDevicesMixin
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
    health: str
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

    def async_fetch_data(self):
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
                                health=x.attrs["State"]
                                .get("Health", dict())
                                .get("Status", None),
                                ports=(
                                    (k + ":" + v[0]["HostPort"])
                                    for k, v in enumerate(
                                        x.attrs["NetworkSettings"]["Ports"]
                                    )
                                ),
                            ),
                        ),
                        containers,
                    )
                ),
            )

        return self.loop.run_in_executor(None, docker_data, self.client)

    def async_container_start(self, id: str):
        return self.loop.run_in_executor(
            None, lambda client, id: client.containers.get(id).start(), self.client, id
        )

    def async_container_stop(self, id: str):
        return self.loop.run_in_executor(
            None, lambda client, id: client.containers.get(id).stop(), self.client, id
        )

    def disconnect(self):
        if self.connected:
            self.client.close()
            self.client = None


class DockerDataUpdateCoordinator(
    AutoDiscoverDevicesMixin, DataUpdateCoordinator[DockerHostInfo]
):
    """Data update coordinator for integration"""

    def __init__(self, hass: HomeAssistant, config_entry: ConfigEntry) -> None:
        super().__init__(
            hass,
            _LOGGER,
            config_entry=config_entry,
            name=DOMAIN,
            update_interval=SCAN_INTERVAL,
        )

        self.api = DockerApi()

    async def _async_update_data(self) -> DockerHostInfo:
        self.clear_new_devices()

        if not self.api.connected:
            await self.api.async_connect()

        data = await self.api.async_fetch_data()
        self.set_new_device_ids(data.containers.keys(), self.config_entry.entry_id)

        return data

    async def async_disconnect(self):
        self._api.disconnect()
        await self.async_shutdown()
