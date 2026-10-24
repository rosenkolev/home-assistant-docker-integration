"""Async Docker Engine API client on top of aiohttp (unix socket or TCP)."""

# cspell:words percpu vsize

from __future__ import annotations

import asyncio
import json
import re
import struct
import typing
from collections.abc import AsyncIterator
from datetime import datetime
from urllib.parse import quote

import aiohttp

from ._models import (
    LABEL_CONFIG_FILES,
    LABEL_HELPER,
    LABEL_NUMBER,
    LABEL_ONEOFF,
    LABEL_PROJECT,
    LABEL_SERVICE,
    LABEL_WORKING_DIR,
    AppInfo,
    ContainerInfo,
    ContainerStats,
    DiskUsage,
    DockerData,
    HostInfo,
    split_image,
)

DEFAULT_SOCKET = "unix:///var/run/docker.sock"
DEFAULT_TIMEOUT = aiohttp.ClientTimeout(total=60, sock_connect=10)
STREAM_TIMEOUT = aiohttp.ClientTimeout(total=None, sock_connect=10, sock_read=None)


class DockerError(Exception):
    """Docker engine returned an error."""

    def __init__(self, status: int, message: str):
        super().__init__(f"{status}: {message}")
        self.status = status
        self.message = message


class DockerConnectionError(DockerError):
    """Docker engine is not reachable."""

    def __init__(self, message: str):
        super().__init__(0, message)


class DockerClient:
    """Minimal async client for the Docker Engine API."""

    def __init__(self, url: str = DEFAULT_SOCKET):
        self.url = url
        self.api_version: str | None = None
        self._session: aiohttp.ClientSession | None = None
        if url.startswith("unix://"):
            self._base = "http://docker"
        else:
            self._base = url.replace("tcp://", "http://", 1).rstrip("/")

    def _create_session(self) -> aiohttp.ClientSession:
        if self.url.startswith("unix://"):
            connector = aiohttp.UnixConnector(path=self.url[len("unix://") :])
        else:
            connector = aiohttp.TCPConnector()
        return aiohttp.ClientSession(connector=connector, timeout=DEFAULT_TIMEOUT)

    @property
    def session(self) -> aiohttp.ClientSession:
        if self._session is None or self._session.closed:
            self._session = self._create_session()
        return self._session

    async def async_connect(self) -> dict:
        """Negotiate the API version and return /version."""
        self.api_version = None
        version = await self._json("GET", "/version")
        self.api_version = version.get("ApiVersion")
        return version

    async def async_close(self) -> None:
        if self._session is not None:
            await self._session.close()
            self._session = None

    def _url(self, path: str) -> str:
        prefix = f"/v{self.api_version}" if self.api_version else ""
        return f"{self._base}{prefix}{path}"

    async def _request(
        self,
        method: str,
        path: str,
        *,
        params: dict | None = None,
        json_body: typing.Any = None,
        data: bytes | None = None,
        headers: dict | None = None,
        timeout: aiohttp.ClientTimeout | None = None,
    ) -> aiohttp.ClientResponse:
        try:
            response = await self.session.request(
                method,
                self._url(path),
                params=_clean_params(params),
                json=json_body,
                data=data,
                headers=headers,
                timeout=timeout or DEFAULT_TIMEOUT,
            )
        except (aiohttp.ClientConnectionError, FileNotFoundError, OSError) as err:
            raise DockerConnectionError(str(err)) from err

        if response.status >= 400:
            try:
                body = await response.json(content_type=None)
                message = body.get("message", str(body))
            except (ValueError, aiohttp.ContentTypeError):
                message = await response.text()
            response.release()
            raise DockerError(response.status, message)

        return response

    async def _json(self, method: str, path: str, **kwargs) -> typing.Any:
        response = await self._request(method, path, **kwargs)
        async with response:
            if response.status == 204:
                return None
            text = await response.text()
            return json.loads(text) if text else None

    # --- system ---

    async def async_info(self) -> dict:
        return await self._json("GET", "/info")

    async def async_disk_usage(self) -> DiskUsage:
        data = await self._json(
            "GET", "/system/df", timeout=aiohttp.ClientTimeout(total=300)
        )
        return parse_disk_usage(data)

    async def async_events(
        self, filters: dict[str, list[str]] | None = None
    ) -> AsyncIterator[dict]:
        """Yield engine events until the connection closes."""
        params = {"filters": json.dumps(filters)} if filters else None
        response = await self._request(
            "GET", "/events", params=params, timeout=STREAM_TIMEOUT
        )
        async with response:
            async for line in response.content:
                line = line.strip()
                if line:
                    yield json.loads(line)

    # --- containers ---

    async def async_containers(self, all: bool = True) -> list[dict]:
        return await self._json("GET", "/containers/json", params={"all": all})

    async def async_container_inspect(self, id: str) -> dict:
        return await self._json("GET", f"/containers/{quote(id)}/json")

    async def async_container_action(self, id: str, action: str) -> None:
        """Run start, stop or restart."""
        await self._json(
            "POST",
            f"/containers/{quote(id)}/{action}",
            timeout=aiohttp.ClientTimeout(total=180),
        )

    async def async_container_remove(self, id: str, volumes: bool = False) -> None:
        """Remove a container; `volumes` also removes its anonymous volumes."""
        await self._json(
            "DELETE", f"/containers/{quote(id)}", params={"force": True, "v": volumes}
        )

    async def async_container_stats(self, id: str) -> ContainerStats:
        data = await self._json(
            "GET", f"/containers/{quote(id)}/stats", params={"stream": False}
        )
        return parse_stats(data)

    async def async_container_logs(
        self,
        id: str,
        tail: int | str = 100,
        since: int | None = None,
        timestamps: bool = False,
        tty: bool = False,
    ) -> str:
        response = await self._request(
            "GET",
            f"/containers/{quote(id)}/logs",
            params={
                "stdout": True,
                "stderr": True,
                "tail": tail,
                "since": since,
                "timestamps": timestamps,
            },
        )
        async with response:
            raw = await response.read()
        return raw.decode("utf-8", "replace") if tty else demux_logs(raw)

    async def async_container_logs_follow(
        self, id: str, tail: int | str = 100, tty: bool = False
    ) -> AsyncIterator[str]:
        """Yield log lines while the container runs."""
        response = await self._request(
            "GET",
            f"/containers/{quote(id)}/logs",
            params={"stdout": True, "stderr": True, "tail": tail, "follow": True},
            timeout=STREAM_TIMEOUT,
        )
        async with response:
            if tty:
                async for line in response.content:
                    yield line.decode("utf-8", "replace").rstrip("\n")
                return

            buffer = ""
            while True:
                try:
                    header = await response.content.readexactly(8)
                except asyncio.IncompleteReadError:
                    break
                size = struct.unpack(">I", header[4:8])[0]
                payload = await response.content.readexactly(size)
                buffer += payload.decode("utf-8", "replace")
                *lines, buffer = buffer.split("\n")
                for line in lines:
                    yield line
            if buffer:
                yield buffer

    async def async_container_rename(self, id: str, name: str) -> None:
        await self._json(
            "POST", f"/containers/{quote(id)}/rename", params={"name": name}
        )

    async def async_container_create(self, name: str | None, config: dict) -> str:
        data = await self._json(
            "POST",
            "/containers/create",
            params={"name": name},
            json_body=config,
        )
        return data["Id"]

    # --- images ---

    async def async_images(self) -> list[dict]:
        return await self._json("GET", "/images/json")

    async def async_image_inspect(self, name: str) -> dict | None:
        try:
            return await self._json("GET", f"/images/{quote(name, safe='')}/json")
        except DockerError as err:
            if err.status == 404:
                return None
            raise

    async def async_image_pull(self, image: str) -> AsyncIterator[dict]:
        """Pull an image and yield progress messages."""
        repository, tag, digest = split_image(image)
        response = await self._request(
            "POST",
            "/images/create",
            params={"fromImage": repository, "tag": digest or tag or "latest"},
            timeout=STREAM_TIMEOUT,
        )
        async with response:
            async for line in response.content:
                line = line.strip()
                if line:
                    message = json.loads(line)
                    if "error" in message:
                        raise DockerError(500, message["error"])
                    yield message

    # --- prune ---

    async def async_prune(self, kind: str, filters: dict | None = None) -> dict:
        """Prune containers, images, volumes or networks."""
        params = {"filters": json.dumps(filters)} if filters else None
        return (
            await self._json(
                "POST",
                f"/{kind}/prune",
                params=params,
                timeout=aiohttp.ClientTimeout(total=600),
            )
            or {}
        )

    async def async_volumes(self) -> list[dict]:
        data = await self._json("GET", "/volumes")
        return data.get("Volumes") or []

    async def async_volume_create(self, body: dict) -> dict:
        return await self._json("POST", "/volumes/create", json_body=body)

    async def async_volume_remove(self, name: str) -> None:
        await self._json("DELETE", f"/volumes/{quote(name, safe='')}")

    # --- networks ---

    async def async_networks(self) -> list[dict]:
        return await self._json("GET", "/networks")

    async def async_network_create(self, body: dict) -> dict:
        return await self._json("POST", "/networks/create", json_body=body)

    async def async_network_connect(
        self, network: str, container: str, endpoint: dict
    ) -> None:
        await self._json(
            "POST",
            f"/networks/{quote(network, safe='')}/connect",
            json_body={"Container": container, "EndpointConfig": endpoint},
        )

    async def async_network_remove(self, id: str) -> None:
        await self._json("DELETE", f"/networks/{quote(id, safe='')}")

    # --- aggregate ---

    async def async_fetch(self) -> DockerData:
        info, containers = await asyncio.gather(
            self.async_info(), self.async_containers(all=True)
        )
        return build_docker_data(info, containers)


def _clean_params(params: dict | None) -> dict | None:
    if params is None:
        return None
    clean = {}
    for key, value in params.items():
        if value is None:
            continue
        clean[key] = ("1" if value else "0") if isinstance(value, bool) else str(value)
    return clean


def demux_logs(raw: bytes) -> str:
    """Decode the multiplexed stdout/stderr log stream of a non-TTY container."""
    out = []
    i = 0
    while i + 8 <= len(raw):
        if raw[i] not in (0, 1, 2) or raw[i + 1 : i + 4] != b"\x00\x00\x00":
            # not multiplexed after all
            return raw.decode("utf-8", "replace")
        size = struct.unpack(">I", raw[i + 4 : i + 8])[0]
        out.append(raw[i + 8 : i + 8 + size])
        i += 8 + size
    return b"".join(out).decode("utf-8", "replace")


def parse_time(value: str | None) -> datetime | None:
    if not value or value.startswith("0001-"):
        return None
    # Docker uses RFC 3339 with nanoseconds; Python takes at most microseconds
    value = re.sub(r"(\.\d{6})\d+", r"\1", value.replace("Z", "+00:00"))
    try:
        return datetime.fromisoformat(value)
    except ValueError:
        return None


def parse_stats(data: dict) -> ContainerStats:
    cpu = data.get("cpu_stats") or {}
    precpu = data.get("precpu_stats") or {}
    cpu_percent = None
    try:
        cpu_delta = cpu["cpu_usage"]["total_usage"] - precpu["cpu_usage"]["total_usage"]
        system_delta = cpu["system_cpu_usage"] - precpu["system_cpu_usage"]
        cpus = cpu.get("online_cpus") or len(
            cpu["cpu_usage"].get("percpu_usage") or [1]
        )
        if system_delta > 0 and cpu_delta >= 0:
            cpu_percent = round(cpu_delta / system_delta * cpus * 100, 2)
    except (KeyError, TypeError):
        pass

    memory = data.get("memory_stats") or {}
    usage = memory.get("usage")
    if usage is not None:
        stats = memory.get("stats") or {}
        # cgroup v2 uses inactive_file, cgroup v1 total_inactive_file
        usage -= stats.get("inactive_file", stats.get("total_inactive_file", 0))

    return ContainerStats(cpu_percent=cpu_percent, memory_usage=usage)


def parse_disk_usage(data: dict) -> DiskUsage:
    images = data.get("Images") or []
    volumes = data.get("Volumes") or []
    cache = data.get("BuildCache") or []

    unused_images = [i for i in images if i.get("Containers", 0) == 0]
    unused_volumes = [
        v for v in volumes if (v.get("UsageData") or {}).get("RefCount", 0) == 0
    ]

    def vsize(v):
        return max((v.get("UsageData") or {}).get("Size", 0), 0)

    return DiskUsage(
        images_size=data.get("LayersSize") or sum(i.get("Size", 0) for i in images),
        images_reclaimable=sum(i.get("Size", 0) for i in unused_images),
        volumes_size=sum(vsize(v) for v in volumes),
        volumes_reclaimable=sum(vsize(v) for v in unused_volumes),
        build_cache_size=sum(c.get("Size", 0) for c in cache),
        volumes_unused=len(unused_volumes),
        images_unused=len(unused_images),
    )


def health_from_status(status: str) -> str | None:
    if "(healthy)" in status:
        return "healthy"
    if "(unhealthy)" in status:
        return "unhealthy"
    if "(health: starting)" in status:
        return "starting"
    return None


def container_key(labels: dict[str, str], name: str) -> tuple[str, str]:
    """Return (container key, app key) for a container."""
    project = labels.get(LABEL_PROJECT)
    service = labels.get(LABEL_SERVICE)
    if project and service:
        number = labels.get(LABEL_NUMBER, "1")
        key = f"{project}/{service}" + ("" if number in ("", "1") else f"-{number}")
        return key, project
    return name, name


def build_docker_data(info: dict, containers: list[dict]) -> DockerData:
    host = HostInfo(
        name=info.get("Name", "Docker"),
        version=info.get("ServerVersion", ""),
        os=info.get("OperatingSystem", ""),
        arch=info.get("Architecture", ""),
        containers=info.get("Containers", 0),
        containers_running=info.get("ContainersRunning", 0),
        containers_paused=info.get("ContainersPaused", 0),
        containers_stopped=info.get("ContainersStopped", 0),
        images=info.get("Images", 0),
    )

    apps: dict[str, AppInfo] = {}
    items: dict[str, ContainerInfo] = {}
    for raw in containers:
        labels = raw.get("Labels") or {}
        if labels.get(LABEL_HELPER) or labels.get(LABEL_ONEOFF) == "True":
            continue

        name = (raw.get("Names") or ["/" + raw["Id"][:12]])[0].lstrip("/")
        key, app_key = container_key(labels, name)
        project = labels.get(LABEL_PROJECT)
        status = raw.get("Status", "")

        ports = sorted(
            {
                f"{p['PublicPort']}:{p['PrivatePort']}/{p.get('Type', 'tcp')}"
                for p in raw.get("Ports") or []
                if p.get("PublicPort")
            }
        )

        items[key] = ContainerInfo(
            key=key,
            id=raw["Id"],
            name=name,
            app_key=app_key,
            project=project,
            service=labels.get(LABEL_SERVICE),
            image=raw.get("Image", ""),
            image_id=raw.get("ImageID", ""),
            state=raw.get("State", ""),
            health=health_from_status(status),
            ports=ports,
            labels=labels,
        )

        app = apps.get(app_key)
        if app is None:
            config_files = labels.get(LABEL_CONFIG_FILES)
            app = apps[app_key] = AppInfo(
                key=app_key,
                name=app_key,
                project=project,
                working_dir=labels.get(LABEL_WORKING_DIR),
                config_files=config_files.split(",") if config_files else [],
            )
        app.container_keys.append(key)

    for app in apps.values():
        app.container_keys.sort()

    return DockerData(host=host, apps=apps, containers=items)


def inspect_details(inspect: dict) -> dict:
    """Details only available from /containers/{id}/json, as ContainerInfo fields."""
    state = inspect.get("State") or {}
    return {
        "started_at": (
            parse_time(state.get("StartedAt")) if state.get("Running") else None
        ),
        "restart_count": inspect.get("RestartCount", 0),
        "tty": bool((inspect.get("Config") or {}).get("Tty")),
    }
