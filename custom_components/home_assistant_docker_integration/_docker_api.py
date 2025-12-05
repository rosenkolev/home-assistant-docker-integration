import asyncio
import re
import typing
from dataclasses import dataclass

import aiohttp
import docker
from docker.errors import ImageNotFound, NotFound

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


def get_version_from_labels(hash: str, labels: list[str] | None):
    return (
        labels["org.opencontainers.image.version"]
        if labels is not None and "org.opencontainers.image.version" in labels
        else hash.split(":", 2)[1][:12]
    )


def parse_image_name(image_name: str) -> tuple[str, str, str]:
    """Parse image name into registry, repository, and tag."""
    parts = image_name.split("/")
    if len(parts) == 1 or (
        "." not in parts[0] and ":" not in parts[0] and parts[0] != "localhost"
    ):
        # Official library or default registry
        registry = "registry-1.docker.io"
        repository = "/".join(parts)
        if len(parts) == 1:
            repository = f"library/{repository}"
    else:
        registry = parts[0]
        repository = "/".join(parts[1:])

    if registry == "docker.io":
        registry = "registry-1.docker.io"

    tag = "latest"
    if ":" in repository:
        repository, tag = repository.split(":", 1)

    return registry, repository, tag


class DockerHttpApi:
    def __init__(self):
        self.session: aiohttp.ClientSession | None = None

    async def async_connect(self):
        self.session = aiohttp.ClientSession()

    async def close(self):
        if self.session:
            await self.session.close()
            self.session = None

    async def get_registry_image_info(self, image_name: str) -> tuple[str | None, dict]:
        """Fetch remote digest and labels from registry directly."""
        if not self.session:
            self.session = aiohttp.ClientSession()

        try:
            registry, repository, tag = parse_image_name(image_name)
            # 1. Get Auth Token
            auth_url = f"https://{registry}/v2/"
            async with self.session.get(auth_url) as resp:
                if resp.status == 401:
                    auth_header = resp.headers.get("Www-Authenticate")
                    if auth_header:
                        match = re.match(
                            r'Bearer realm="([^"]+)",service="([^"]+)"', auth_header
                        )
                        if match:
                            realm = match.group(1)
                            service = match.group(2)
                            scope = f"repository:{repository}:pull"
                            token_url = f"{realm}?service={service}&scope={scope}"
                            async with self.session.get(token_url) as token_resp:
                                if token_resp.status == 200:
                                    token_data = await token_resp.json()
                                    token = token_data.get("token") or token_data.get(
                                        "access_token"
                                    )
                                    headers = {"Authorization": f"Bearer {token}"}
                                else:
                                    _LOGGER.warning(
                                        f"Failed to get auth token for {image_name}: {token_resp.status}"
                                    )
                                    return None, {}
                        else:
                            # Try parsing without regex if it's simpler or different fmt
                            pass

            api_base = f"https://{registry}/v2/{repository}"
            headers = headers if "headers" in locals() else {}
            # Accept both V2 and OCI manifests
            headers["Accept"] = (
                "application/vnd.docker.distribution.manifest.v2+json, "
                "application/vnd.oci.image.manifest.v1+json, "
                "application/vnd.docker.distribution.manifest.list.v2+json, "
                "application/vnd.oci.image.index.v1+json"
            )

            # 2. Get Manifest
            _LOGGER.debug(f"Fetching manifest from {api_base}/manifests/{tag}")
            remote_labels = {}
            async with self.session.get(
                f"{api_base}/manifests/{tag}", headers=headers
            ) as resp:
                if resp.status != 200:
                    _LOGGER.warning(
                        f"Failed to get manifest for {image_name}: {resp.status}"
                    )
                    return None, {}

                # Check Digest Header
                remote_digest = resp.headers.get("Docker-Content-Digest")
                if not remote_digest:
                    # Calculate or read from body? usually header is best for v2
                    pass

                manifest = await resp.json()
                manifests = manifest.get("manifests", [])
                annotations = manifest.get("annotations", {})
                for manifest in manifests:
                    # combine manifest.annotations as labels
                    remote_labels.update(manifest.get("annotations", {}))

                # combine annotations as labels
                remote_labels.update(annotations)

            return remote_digest, remote_labels

        except Exception as e:
            _LOGGER.warning(f"Error checking registry for {image_name}: {e}")
            return None, {}


class DockerApi:
    def __init__(self):
        self.base_url = "unix://var/run/docker.sock"
        self.loop = asyncio.get_running_loop()
        self.client = None
        self.http = DockerHttpApi()

    @property
    def connected(self) -> bool:
        return self.client is not None and isinstance(self.client, docker.DockerClient)

    async def async_connect(self) -> None:
        def docker_client_init(obj):
            obj.client = docker.DockerClient(base_url=obj.base_url)

        await self.loop.run_in_executor(None, docker_client_init, self)
        await self.http.async_connect()

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
        restart_policy: dict = None,
    ):
        return self.loop.run_in_executor(
            None,
            lambda client, image, name, ports, network, volumes, restart_policy: client.containers.create(
                image,
                detach=True,
                name=name,
                ports=ports,
                network=network,
                volumes=volumes,
                restart_policy=restart_policy,
            ),
            self.client,
            image,
            name,
            ports,
            network,
            volumes,
            restart_policy,
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

    async def async_images_check_update(self, image_name: str) -> DockerImageUpdateInfo:
        """
        Check if a newer version of the image exists on the registry.
        """
        _LOGGER.debug(f"async_images_check_update: {image_name}")

        def get_local_info(client, image_name: str) -> tuple[str | None, dict | None]:
            try:
                local_image = client.images.get(image_name)
                local_digest = local_image.attrs.get("RepoDigests", [None])[0]

                if not local_digest:
                    return None, None

                local_digest_hash = local_digest.split("@")[1]
                local_labels = local_image.attrs.get("Config", {}).get("Labels") or {}
                return local_digest_hash, local_labels
            except ImageNotFound:
                return None, None
            except Exception as e:
                _LOGGER.warning(f"Error getting local image info for {image_name}: {e}")
                raise e

        # Fallback: Use blocking docker-py check which handles auth better sometimes?
        def fallback_get_registry_image_info(client, image_name):
            try:
                registry_data = client.images.get_registry_data(image_name)
                return registry_data.id, {}  # No labels on registry_data
            except Exception:
                return None, {}

        info = DockerImageUpdateInfo(
            has_newer=False, current_ver=None, new_ver=None, source=None
        )

        # 1. Get Local Info
        try:
            local_digest_hash, local_labels = await self.loop.run_in_executor(
                None, get_local_info, self.client, image_name
            )
        except Exception:
            # If error happens, assume we can't check
            return info

        if not local_digest_hash:
            # Not found or no digest, assume update available (standard behavior)
            info.has_newer = True
            return info

        info.source = local_labels.get("org.opencontainers.image.source")
        info.current_ver = get_version_from_labels(local_digest_hash, local_labels)

        # 2. Check Registry
        # Fallback to docker-py logic if my custom logic fails?
        # Actually logic is robust enough to return None.
        _LOGGER.debug(f"Checking registry for {image_name}...")
        remote_digest_hash, remote_labels = await self.http.get_registry_image_info(
            image_name
        )
        _LOGGER.debug(f"Remote digest (http): {remote_digest_hash}:::{remote_labels}")

        if not remote_digest_hash:
            _LOGGER.debug("Falling back to docker client for registry info")
            remote_digest_hash, remote_labels = await self.loop.run_in_executor(
                None, fallback_get_registry_image_info, self.client, image_name
            )
            _LOGGER.debug(f"Remote digest (fallback): {remote_digest_hash}")

        if remote_digest_hash:
            info.has_newer = local_digest_hash != remote_digest_hash
            _LOGGER.debug(
                f"Has newer: {info.has_newer} (Local: {local_digest_hash} vs Remote: {remote_digest_hash})"
            )
            if info.has_newer:
                info.new_ver = get_version_from_labels(
                    remote_digest_hash, remote_labels
                )
                _LOGGER.debug(
                    f"Update found for {image_name}: {info.current_ver} -> {info.new_ver}"
                )

        return info

    async def disconnect(self):
        await self.http.close()

        if self.connected:
            self.client.close()
            self.client = None
