import asyncio
import typing
from dataclasses import dataclass

import docker
from docker.errors import APIError, ImageNotFound, NotFound

from .const import _LOGGER


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


@dataclass()
class DockerImageUpdateInfo:
    has_newer: bool
    current_ver: str
    new_ver: str
    source: str


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
            # df = client.df()
            # _LOGGER.warning(df)
            pass

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

    def async_container_remove(self, id: str, remove_volumes=False):
        return self.loop.run_in_executor(
            None,
            lambda client, id, volumes: client.containers.get(id).remove(v=volumes),
            self.client,
            id,
            remove_volumes,
        )

    def async_container_create(
        self,
        image: str,
        name: str = None,
        ports: dict = None,
        network: str = None,
        volumes: list = None,
    ):
        return self.loop.run_in_executor(
            None,
            lambda client, image, name, ports, network, volumes: client.containers.create(
                image,
                detach=True,
                name=name,
                ports=ports,
                network=network,
                volumes=volumes,
            ),
            self.client,
            image,
            name,
            ports,
            network,
            volumes,
        )

    def async_volumes_prune(self):
        return self.loop.run_in_executor(
            None, lambda client: client.volumes.prune(), self.client
        )

    def async_images_prune(self, dangling=False):
        return self.loop.run_in_executor(
            None,
            lambda client, dangling: client.images.prune({"dangling": dangling}),
            self.client,
            dangling,
        )

    def async_containers_prune(self):
        return self.loop.run_in_executor(
            None, lambda client: client.containers.prune(), self.client
        )

    def async_container_update(self, id: str) -> bool:
        def _update_container(client, id: str):
            try:
                container = client.containers.get(id)
            except NotFound:
                _LOGGER.warning(f"Container '{id}' not found")
                return False

            name = container.name
            attrs = container.attrs
            config = attrs.get("Config", {})
            image_name = config.get("Image", None)
            if not image_name:
                _LOGGER.warning(f"No image for container {name}")
                return False

            _LOGGER.debug(f"Updating container '{name}' to latest '{image_name}'")
            client.images.pull(image_name)

            # Extract options
            host_cfg = attrs.get("HostConfig", {})
            networking = attrs.get("NetworkSettings", {}).get("Networks", {})
            env = config.get("Env", [])
            cmd = config.get("Cmd", None)
            entrypoint = config.get("Entrypoint", None)
            working_dir = config.get("WorkingDir", None)
            labels = config.get("Labels", None)
            user = config.get("User", None)
            restart_policy = host_cfg.get("RestartPolicy", {})
            ports = {
                p.split("/")[0]: binding[0]["HostPort"]
                for p, binding in (host_cfg.get("PortBindings", {}) or {}).items()
            } or None
            mounts = {
                m["Source"]: {
                    "bind": m["Destination"],
                    "mode": "rw" if m.get("RW", True) else "ro",
                }
                for m in attrs.get("Mounts", [])
            }

            _LOGGER.debug("Stopping and removing old container...")
            container.stop()
            container.remove()

            _LOGGER.debug("Creating new container with preserved settings...")
            new_container = client.containers.run(
                image_name,
                name=name,
                detach=True,
                environment=env,
                ports=ports,
                volumes=mounts,
                restart_policy=restart_policy,
                working_dir=working_dir,
                command=cmd,
                entrypoint=entrypoint,
                labels=labels,
                user=user,
            )

            # attach networks
            for net_name in networking.keys():
                try:
                    client.networks.get(net_name).connect(new_container)
                except Exception as e:
                    _LOGGER.warning(f"Failed to reattach network '{net_name}': {e}")

            return True

        return self.loop.run_in_executor(None, _update_container, self.client, id)

    def async_images_check_update(self, image_name: str) -> DockerImageUpdateInfo:
        """
        Check if a newer version of the image exists on the registry.
        """

        def check_for_image_update(client, image_name: str) -> bool:
            info = DockerImageUpdateInfo(False, None, None, None)
            try:
                local_image = client.images.get(image_name)
                local_digest = local_image.attrs.get("RepoDigests", [None])[0]
                if not local_digest:
                    info.has_newer = True
                    return info

                local_digest_hash = local_digest.split("@")[1]
                local_labels = local_image.attrs.get("Config", {}).get("Labels") or {}
                info.source = local_labels.get("org.opencontainers.image.source")
                info.current_ver = (
                    local_labels.get("org.opencontainers.image.version")
                    or local_digest_hash.split(":", 2)[1][:12]
                )

                registry_data = client.images.get_registry_data(image_name)
                remote_digest_hash = registry_data.id
                remote_labels = {}
                info.has_newer = local_digest_hash != remote_digest_hash
                if info.has_newer:
                    id = remote_digest_hash.split(":", 2)[1]
                    _LOGGER.warning("has newer: " + id)
                    remote_data = client.api.inspect_image(remote_digest_hash)
                    _LOGGER.warning(remote_data)
                    remote_labels = remote_data.get("Labels") or {}

                info.new_ver = (
                    remote_labels.get("org.opencontainers.image.version")
                    or remote_digest_hash.split(":", 2)[1][:12]
                )
                # remote_version = registry_data.attrs.get("Labels", {}).get(
                #     "org.opencontainers.image.version"
                # )
                # _LOGGER.warning(local_image.attrs)
                # _LOGGER.warning(registry_data.attrs)
                _LOGGER.warning(
                    f"image-versions l_hash:{local_digest_hash} l_ver:{info.current_ver}, r_hash:{remote_digest_hash}, r_ver:{info.new_ver}"
                )
            except ImageNotFound:
                # image not found locally so update/pull it
                info.has_newer = True
            except APIError as e:
                _LOGGER.warning(f"Could not fetch registry data for {image_name}: {e}")

            return info

        return self.loop.run_in_executor(
            None, check_for_image_update, self.client, image_name
        )

    def disconnect(self):
        if self.connected:
            self.client.close()
            self.client = None
