import asyncio
import typing
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
    state: typing.Literal["restarting", "running", "paused", "exited"]
    status: str
    image_id: str
    image_name: str
    compose_project: typing.Optional[str]
    short_id: str
    ports: list[str]
    mounts: list[str]


@dataclass(kw_only=True)
class DockerImageInfo:
    id: str
    tag: str
    title: str
    rev: str
    description: str
    in_use: bool


@dataclass(kw_only=True)
class DockerVolumeInfo:
    name: str
    in_use: bool
    size: str
    mount_point: str


@dataclass(kw_only=True)
class DockerHostInfo:
    version: str
    containers_total: int
    containers_running: int
    images_total: int
    firewall: str
    containers: dict[str, DockerContainerInfo]
    images: dict[str, DockerImageInfo]
    volumes: dict[str, DockerVolumeInfo]


DOCKER_DATA_KEYS = typing.Literal["containers", "images", "volumes"]


def get_img_id(id: str):
    return id.split(":", 1)[1]


def get_label(key: str, labels: list[str] | None):
    return labels[key] if labels is not None and key in labels else None


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
            data: dict = client.df()
            containers = map(
                lambda x: DockerContainerInfo(
                    id=x["Id"],
                    short_id=x["Id"][:12],
                    name=x["Names"][0][1:],
                    state=x["State"],
                    status=x["Status"],
                    image_id=get_img_id(x["ImageID"]),
                    image_name=x["Image"],
                    compose_project=(
                        x["Labels"]["com.docker.compose.project"]
                        if ("com.docker.compose.project" in x["Labels"])
                        else None
                    ),
                    ports=set(
                        (
                            str(p["PrivatePort"])
                            + ":"
                            + (
                                str(p["PublicPort"])
                                if "PublicPort" in p
                                else str(p["PrivatePort"])
                            )
                        )
                        for p in x["Ports"]
                    ),
                    mounts=set(
                        (
                            ("b:" if o["Type"] == "bind" else (f"v({o["Name"]}):"))
                            + o["Source"]
                            + ":"
                            + o["Destination"]
                            + ":"
                            + o["Mode"]
                        )
                        for o in x["Mounts"]
                    ),
                ),
                data.get("Containers", []),
            )

            images = map(
                lambda x: DockerImageInfo(
                    id=get_img_id(x["Id"]),
                    tag=x["RepoTags"][0] if x["RepoTags"] else None,
                    title=get_label("org.opencontainers.image.title", x["Labels"]),
                    rev=get_label("org.opencontainers.image.revision", x["Labels"]),
                    description=get_label(
                        "org.opencontainers.image.description", x["Labels"]
                    ),
                    in_use=x["Containers"] > 0,
                ),
                data.get("Images", []),
            )

            volumes = map(
                lambda x: DockerVolumeInfo(
                    name=x["Name"],
                    in_use=x["UsageData"]["RefCount"] > 0,
                    size=str(round(x["UsageData"]["Size"] / 1024, 2)) + "KB",
                    mount_point=x["Mountpoint"],
                ),
                data.get("Volumes", []),
            )

            return DockerHostInfo(
                version=info["ServerVersion"],
                firewall=info["FirewallBackend"]["Driver"],
                containers_total=info["Containers"],
                containers_running=info["ContainersRunning"],
                images_total=info["Images"],
                containers=dict(map(lambda x: (x.short_id, x), containers)),
                images=dict(map(lambda x: (x.id[:12], x), images)),
                volumes=dict(map(lambda x: (x.name[:26], x), volumes)),
            )

        return self.loop.run_in_executor(None, docker_data, self.client)

    def async_test(self):
        def action(client):
            pass
            # df = client.df()
            # _LOGGER.warning(df)
            # data = client.images.list()
            # _LOGGER.warning(data[0])

        return self.loop.run_in_executor(None, action, self.client)

    def async_container_start(self, id: str):
        return self.loop.run_in_executor(
            None, lambda client, id: client.containers.get(id).start(), self.client, id
        )

    def async_container_stop(self, id: str):
        return self.loop.run_in_executor(
            None, lambda client, id: client.containers.get(id).stop(), self.client, id
        )

    def async_container_restart(self, id: str):
        return self.loop.run_in_executor(
            None,
            lambda client, id: client.containers.get(id).restart(),
            self.client,
            id,
        )

    def disconnect(self):
        if self.connected:
            self.client.close()
            self.client = None


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

        self.api = DockerApi()
        self.tracker = DeviceTracker(config_entry.entry_id)
        self.data: DockerHostInfo = {}
        self._test = False

    async def _async_update_data(self) -> DockerHostInfo:
        self.tracker.reset_added_devices()

        if not self.api.connected:
            await self.api.async_connect()

        data = await self.api.async_fetch_data()

        self.tracker.set_device_ids(
            set(data.containers.keys()),
            set(data.volumes.keys()),
            set(data.images.keys()),
        )

        if self._test:
            self._test = False
            await self.api.async_test()

        return data

    async def async_disconnect(self):
        self.api.disconnect()
        await self.async_shutdown()
