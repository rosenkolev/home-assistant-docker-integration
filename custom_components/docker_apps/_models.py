"""Data models shared by the Docker client, the coordinators and the entities."""

# cspell:words opencontainers

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime

LABEL_PROJECT = "com.docker.compose.project"
LABEL_SERVICE = "com.docker.compose.service"
LABEL_NUMBER = "com.docker.compose.container-number"
LABEL_WORKING_DIR = "com.docker.compose.project.working_dir"
LABEL_CONFIG_FILES = "com.docker.compose.project.config_files"
LABEL_ONEOFF = "com.docker.compose.oneoff"
LABEL_HELPER = "io.github.rosenkolev.docker_apps.helper"
LABEL_OCI_VERSION = "org.opencontainers.image.version"


@dataclass(kw_only=True)
class ContainerInfo:
    """A container, keyed by its stable `key` (project/service or name)."""

    key: str
    id: str
    name: str
    app_key: str
    project: str | None
    service: str | None
    image: str
    image_id: str
    state: str
    health: str | None = None
    started_at: datetime | None = None
    restart_count: int = 0
    tty: bool = False
    ports: list[str] = field(default_factory=list)
    labels: dict[str, str] = field(default_factory=dict)

    @property
    def short_id(self) -> str:
        return self.id[:12]

    @property
    def running(self) -> bool:
        return self.state == "running"

    @property
    def image_version(self) -> str | None:
        return self.labels.get(LABEL_OCI_VERSION) or image_tag(self.image)


@dataclass(kw_only=True)
class AppInfo:
    """A group of containers: a compose project, or a single standalone container."""

    key: str
    name: str
    project: str | None
    working_dir: str | None = None
    config_files: list[str] = field(default_factory=list)
    container_keys: list[str] = field(default_factory=list)

    @property
    def is_compose(self) -> bool:
        return self.project is not None


@dataclass(kw_only=True)
class HostInfo:
    name: str
    version: str
    os: str
    arch: str
    containers: int
    containers_running: int
    containers_paused: int
    containers_stopped: int
    images: int


@dataclass(kw_only=True)
class ContainerStats:
    cpu_percent: float | None
    memory_usage: int | None


@dataclass(kw_only=True)
class DiskUsage:
    images_size: int = 0
    images_reclaimable: int = 0
    volumes_size: int = 0
    volumes_reclaimable: int = 0
    build_cache_size: int = 0
    volumes_unused: int = 0
    images_unused: int = 0


@dataclass(kw_only=True)
class DockerData:
    host: HostInfo
    apps: dict[str, AppInfo]
    containers: dict[str, ContainerInfo]

    def app_containers(self, app_key: str) -> list[ContainerInfo]:
        app = self.apps.get(app_key)
        if app is None:
            return []
        return [self.containers[k] for k in app.container_keys if k in self.containers]


def split_image(image: str) -> tuple[str, str | None, str | None]:
    """Split `repo[:tag][@digest]` into (repository, tag, digest)."""
    digest = None
    if "@" in image:
        image, digest = image.split("@", 1)

    tag = None
    last = image.rsplit("/", 1)[-1]
    if ":" in last:
        image, tag = image.rsplit(":", 1)

    return image, tag, digest


def image_tag(image: str) -> str | None:
    return split_image(image)[1] or ("latest" if "@" not in image else None)
