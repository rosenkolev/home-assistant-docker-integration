"""In-memory Docker engine used by the tests instead of a real socket."""

# cspell:words Armbian NCPU

from __future__ import annotations

import asyncio
import itertools

from custom_components.docker_apps._docker_client import DockerError, build_docker_data
from custom_components.docker_apps._models import ContainerStats, DiskUsage

_ids = itertools.count(1)


def new_id() -> str:
    return f"{next(_ids):064x}"


class FakeDocker:
    def __init__(self, url: str = "unix:///var/run/docker.sock"):
        self.url = url
        self.api_version = "1.47"
        self.containers: dict[str, dict] = {}
        self.images: dict[str, dict] = {}
        self.networks: dict[str, dict] = {
            "bridge": {"Id": "bridge", "Name": "bridge", "Labels": {}}
        }
        self.volumes: dict[str, dict] = {}
        self.calls: list[tuple] = []
        self.logs = "line 1\nline 2\n"
        self.events: asyncio.Queue = asyncio.Queue()

    # --- helpers for tests ---

    def add_image(self, ref: str, digest: str | None = None) -> str:
        image_id = "sha256:" + new_id()
        repo = (
            ref.split("@")[0].rsplit(":", 1)[0]
            if ":" in ref.rsplit("/", 1)[-1]
            else ref
        )
        self.images[ref] = {
            "Id": image_id,
            "RepoTags": [ref],
            "RepoDigests": [f"{repo}@{digest}"] if digest else [],
            "Size": 1000,
            "Created": 0,
        }
        return image_id

    def add_container(
        self,
        name: str,
        image: str,
        project: str | None = None,
        service: str | None = None,
        working_dir: str | None = None,
        state: str = "running",
        ports: list | None = None,
        labels: dict | None = None,
    ) -> str:
        image_id = self.images.get(image, {}).get("Id") or self.add_image(image)
        all_labels = dict(labels or {})
        if project:
            all_labels.update(
                {
                    "com.docker.compose.project": project,
                    "com.docker.compose.service": service or name,
                    "com.docker.compose.container-number": "1",
                    "com.docker.compose.oneoff": "False",
                }
            )
            if working_dir:
                all_labels["com.docker.compose.project.working_dir"] = working_dir
                all_labels["com.docker.compose.project.config_files"] = (
                    f"{working_dir}/compose.yaml"
                )
        cid = new_id()
        self.containers[cid] = {
            "Id": cid,
            "Names": [f"/{name}"],
            "Image": image,
            "ImageID": image_id,
            "Labels": all_labels,
            "State": state,
            "Status": "Up 1 hour" if state == "running" else "Exited (0)",
            "Ports": ports or [],
            "Config": {},
        }
        return cid

    # --- client API ---

    async def async_connect(self) -> dict:
        return {"Version": "27.5.1", "ApiVersion": self.api_version}

    async def async_close(self) -> None:
        pass

    async def async_info(self) -> dict:
        running = sum(c["State"] == "running" for c in self.containers.values())
        return {
            "ID": "host",
            "Name": "nas",
            "ServerVersion": "27.5.1",
            "OperatingSystem": "Armbian",
            "Architecture": "aarch64",
            "KernelVersion": "6.1",
            "NCPU": 8,
            "MemTotal": 8 * 1024**3,
            "Containers": len(self.containers),
            "ContainersRunning": running,
            "ContainersPaused": 0,
            "ContainersStopped": len(self.containers) - running,
            "Images": len(self.images),
        }

    async def async_fetch(self):
        return build_docker_data(await self.async_info(), await self.async_containers())

    async def async_containers(self, all: bool = True) -> list[dict]:
        return [dict(c) for c in self.containers.values()]

    def _get(self, id: str) -> dict:
        for cid, c in self.containers.items():
            if cid == id or cid.startswith(id) or c["Names"][0] == f"/{id}":
                return c
        raise DockerError(404, f"No such container: {id}")

    async def async_container_inspect(self, id: str) -> dict:
        c = self._get(id)
        running = c["State"] == "running"
        return {
            "Id": c["Id"],
            "Image": c["ImageID"],
            "State": {
                "Status": c["State"],
                "Running": running,
                "StartedAt": "2026-10-23T10:00:00.123456789Z",
                "ExitCode": 0,
                "OOMKilled": False,
            },
            "RestartCount": 0,
            "Config": {"Labels": c["Labels"], "Tty": False, **c.get("Config", {})},
            "HostConfig": c.get("HostConfig", {}),
            "Mounts": c.get("Mounts", []),
        }

    async def async_container_action(self, id: str, action: str) -> None:
        c = self._get(id)
        self.calls.append((action, c["Names"][0][1:]))
        if action in ("start", "restart", "unpause"):
            c["State"] = "running"
        elif action in ("stop", "kill"):
            c["State"] = "exited"

    async def async_container_remove(self, id: str, volumes: bool = False) -> None:
        c = self._get(id)
        self.calls.append(("remove", c["Names"][0][1:]))
        del self.containers[c["Id"]]

    async def async_container_stats(self, id: str) -> ContainerStats:
        return ContainerStats(cpu_percent=1.5, memory_usage=100 * 1048576)

    async def async_container_logs(
        self, id, tail=100, since=None, timestamps=False, tty=False
    ) -> str:
        return self.logs

    async def async_container_logs_follow(self, id, tail=100, tty=False):
        for line in self.logs.splitlines():
            yield line

    async def async_container_create(self, name: str | None, config: dict) -> str:
        for c in self.containers.values():
            if name and c["Names"][0] == f"/{name}":
                raise DockerError(
                    409, f"Conflict. The container name /{name} is already in use"
                )
        image = config["Image"]
        if image not in self.images:
            raise DockerError(404, f"No such image: {image}")
        cid = new_id()
        ports = []
        for key, bindings in (
            config.get("HostConfig", {}).get("PortBindings") or {}
        ).items():
            private, _, proto = key.partition("/")
            for b in bindings:
                if b.get("HostPort"):
                    ports.append(
                        {
                            "PrivatePort": int(private),
                            "PublicPort": int(b["HostPort"]),
                            "Type": proto,
                        }
                    )
        self.containers[cid] = {
            "Id": cid,
            "Names": [f"/{name or cid[:12]}"],
            "Image": image,
            "ImageID": self.images[image]["Id"],
            "Labels": dict(config.get("Labels") or {}),
            "State": "created",
            "Status": "Created",
            "Ports": ports,
            "Config": config,
            "HostConfig": config.get("HostConfig", {}),
        }
        self.calls.append(("create", name))
        return cid

    async def async_image_inspect(self, name: str) -> dict | None:
        if name in self.images:
            return self.images[name]
        for image in self.images.values():
            if image["Id"] == name:
                return image
        return None

    async def async_image_pull(self, image: str):
        self.calls.append(("pull", image))
        if image not in self.images:
            self.add_image(image)
        yield {"status": "Downloaded"}

    async def async_images(self) -> list[dict]:
        return list(self.images.values())

    async def async_prune(self, kind: str, filters=None) -> dict:
        self.calls.append(("prune", kind))
        return {"SpaceReclaimed": 10}

    async def async_volumes(self) -> list[dict]:
        return list(self.volumes.values())

    async def async_volume_create(self, body: dict) -> dict:
        self.volumes[body["Name"]] = {**body, "Driver": body.get("Driver", "local")}
        self.calls.append(("volume_create", body["Name"]))
        return self.volumes[body["Name"]]

    async def async_volume_remove(self, name: str) -> None:
        self.calls.append(("volume_remove", name))
        self.volumes.pop(name, None)

    async def async_networks(self) -> list[dict]:
        return list(self.networks.values())

    async def async_network_create(self, body: dict) -> dict:
        self.networks[body["Name"]] = {"Id": body["Name"], **body}
        self.calls.append(("network_create", body["Name"]))
        return {"Id": body["Name"]}

    async def async_network_connect(
        self, network: str, container: str, endpoint: dict
    ) -> None:
        self.calls.append(("network_connect", network))

    async def async_network_remove(self, id: str) -> None:
        self.networks.pop(id, None)

    async def async_disk_usage(self) -> DiskUsage:
        return DiskUsage(images_size=5000, images_reclaimable=2000)

    async def async_events(self, filters=None):
        while True:
            yield await self.events.get()


class FakeRegistry:
    def __init__(
        self,
        tags: dict[str, list[str]] | None = None,
        digests: dict[str, str] | None = None,
    ):
        self.tags = tags or {}
        self.digests = digests or {}

    async def async_tags(self, image: str) -> list[str]:
        repo = image.rsplit(":", 1)[0] if ":" in image.rsplit("/", 1)[-1] else image
        return self.tags.get(repo, [])

    async def async_digest(self, image: str) -> str | None:
        return self.digests.get(image)

    def clear_cache(self) -> None:
        pass
