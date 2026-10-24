"""App lifecycle: managed state, update checks, updates, rollback, store installs."""

# cspell:words clickhouse keydb opensearch pgvecto REUSEADDR

from __future__ import annotations

import asyncio
import json
import re
import socket
from collections.abc import Callable
from contextlib import asynccontextmanager
from dataclasses import asdict, dataclass, field
from datetime import UTC, datetime
from pathlib import PurePosixPath

import yaml
from homeassistant.core import HomeAssistant
from homeassistant.helpers.storage import Store

from ._compose import (
    ComposeEngine,
    ComposeError,
    ComposeModel,
    as_mapping,
    interpolate,
    load_model,
)
from ._compose_file import parse_services, update_service_tag
from ._docker_client import DockerClient, DockerError, health_from_status
from ._models import LABEL_PROJECT, LABEL_SERVICE, AppInfo, DockerData, split_image
from ._paths import PathMap, copy_file, read_text, remove_tree, write_text
from ._registry import RegistryClient, RegistryError
from ._store import StoreApp, format_env, published_ports, render_app, safe_id
from ._versions import is_floating, newest_tag, parse_tag, short_digest
from .const import _LOGGER, DOMAIN

STORAGE_VERSION = 1
HISTORY_KEEP = 3
HEALTH_TIMEOUT = 300
META_DIR = ".docker-apps"
OVERRIDE_FILE = "compose.override.yaml"
OVERRIDE_HEADER = "# Your changes to this app. Kept across updates.\n"
# Data stores: a new major version needs a manual upgrade, so it holds app updates.
DATABASE_IMAGES = (
    "postgres",
    "pgvecto",
    "vectorchord",
    "mysql",
    "mariadb",
    "mongo",
    "elasticsearch",
    "opensearch",
    "influxdb",
    "clickhouse",
)
# Caches: not updated on their own, but updated with the app when it needs them.
CACHE_IMAGES = ("redis", "valkey", "memcached", "keydb", "dragonfly")
DEFAULT_UNTRACKED = DATABASE_IMAGES + CACHE_IMAGES
PRIMARY_HINTS = ("server", "app", "web", "main")
OWN_CONTAINER = re.compile(r"/containers/([0-9a-f]{64})/hostname ")


class AppError(Exception):
    """An app operation failed."""


@dataclass
class ServiceUpdate:
    service: str
    image: str
    current: str
    latest: str
    kind: str  # "tag" or "digest"
    tracked: bool = True
    required: bool = False  # needed by the new app version (upstream compose)

    @property
    def has_update(self) -> bool:
        return self.latest != self.current


@dataclass
class AppUpdate:
    app_key: str
    installed_version: str | None
    latest_version: str | None
    services: list[ServiceUpdate] = field(default_factory=list)
    store_app: StoreApp | None = None
    error: str | None = None
    blocked: str | None = None  # why the update can't be installed automatically

    @property
    def targets(self) -> list[ServiceUpdate]:
        """Services the package update changes."""
        return [s for s in self.services if s.has_update and (s.tracked or s.required)]

    @property
    def has_update(self) -> bool:
        if self.store_app is not None:
            return self.latest_version != self.installed_version
        return any(s.tracked for s in self.targets)

    def summary(self) -> str:
        if self.store_app is not None:
            return f"{self.store_app.name} {self.installed_version} → {self.latest_version}"
        text = ", ".join(
            f"{s.service}: {s.current} → {s.latest}"
            + (" (required)" if s.required else "")
            for s in self.targets
        )
        return f"{text}. On hold: {self.blocked}" if self.blocked else text


@dataclass
class AppSettings:
    managed: bool = False
    # tracked services; None means every service except untracked images
    services: list[str] | None = None
    include: str | None = None
    exclude: str | None = None
    upstream: str | None = None  # upstream compose URL with {version}
    allow_db_minor: bool = False
    # app launcher card; None means on for store apps, off for found projects
    launcher: bool | None = None
    store: dict | None = None
    history: list[dict] = field(default_factory=list)


def image_name(image: str) -> str:
    return split_image(image)[0].rsplit("/", 1)[-1].lower()


def is_database_image(image: str) -> bool:
    return any(hint in image_name(image) for hint in DATABASE_IMAGES)


def major(tag: str | None) -> int | None:
    parsed = parse_tag(tag or "")
    return parsed[1][0] if parsed else None


def own_container_id(mountinfo: str = "/proc/self/mountinfo") -> str | None:
    """Return the id of the container Home Assistant runs in, if any.

    Docker bind-mounts `<data-root>/containers/<id>/hostname` into every container.
    The data root is not always /var/lib/docker, so only the tail is matched.
    """
    try:
        with open(mountinfo, encoding="utf-8") as file:
            match = OWN_CONTAINER.search(file.read())
    except OSError:
        return None
    return match[1] if match else None


def host_ports_in_use(ports: set[int]) -> set[int]:
    """Ports that something on this host already listens on (TCP).

    Only meaningful when Home Assistant runs with the host network: it then sees
    the ports of host processes and of host-network containers too.
    """
    busy = set()
    for port in ports:
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
            sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
            try:
                sock.bind(("0.0.0.0", port))  # noqa: S104
            except OSError:
                busy.add(port)
    return busy


def primary_service(
    app_key: str, services: list[ServiceUpdate]
) -> ServiceUpdate | None:
    """The service whose version names the app: same name, then a hint, then first."""
    tagged = [s for s in services if s.kind == "tag"] or services
    for service in tagged:
        if service.service == app_key:
            return service
    for hint in PRIMARY_HINTS:
        for service in tagged:
            if hint in service.service:
                return service
    return tagged[0] if tagged else None


class AppManager:
    """Owns managed-app state and runs app operations."""

    def __init__(
        self,
        hass: HomeAssistant,
        entry_id: str,
        client: DockerClient,
        registry: RegistryClient,
        apps_root: str,
        data_root: str,
        untracked: list[str] | None = None,
        http=None,
    ):
        self.hass = hass
        self.client = client
        self.registry = registry
        self.http = http
        if untracked is None:
            untracked = DEFAULT_UNTRACKED
        self.untracked = [u.lower() for u in untracked]
        self.engine = ComposeEngine(client)
        self.apps_root = apps_root.rstrip("/")
        self.data_root = data_root.rstrip("/")
        self.settings: dict[str, AppSettings] = {}
        self.busy: dict[str, str] = {}
        # app key -> store app id, while a store app is being installed
        self.installing: dict[str, str] = {}
        self.progress: dict[str, float | None] = {}
        self.self_container_id: str | None = None
        self.self_inspect: dict | None = None
        self.paths = PathMap(None)
        self._store = Store(hass, STORAGE_VERSION, f"{DOMAIN}.{entry_id}.apps")
        self._listeners: list[Callable[[], None]] = []
        self._locks: dict[str, asyncio.Lock] = {}

    # --- state ---

    async def async_setup(self) -> None:
        data = await self._store.async_load() or {}
        self.settings = {
            key: AppSettings(**value) for key, value in (data.get("apps") or {}).items()
        }
        self.self_container_id = await self.hass.async_add_executor_job(
            own_container_id
        )
        if self.self_container_id:
            try:
                self.self_inspect = await self.client.async_container_inspect(
                    self.self_container_id
                )
            except DockerError as err:
                _LOGGER.warning("Can't inspect the Home Assistant container: %s", err)
        self.paths = PathMap.from_inspect(self.self_inspect)

    async def async_save(self) -> None:
        await self._store.async_save(
            {"apps": {key: asdict(value) for key, value in self.settings.items()}}
        )

    def add_listener(self, listener: Callable[[], None]) -> Callable[[], None]:
        self._listeners.append(listener)
        return lambda: self._listeners.remove(listener)

    def _notify(self) -> None:
        for listener in list(self._listeners):
            listener()

    def get(self, app_key: str) -> AppSettings:
        return self.settings.get(app_key) or AppSettings()

    def is_managed(self, app_key: str) -> bool:
        return self.get(app_key).managed

    def in_launcher(self, app_key: str) -> bool:
        settings = self.get(app_key)
        return (
            settings.launcher if settings.launcher is not None else bool(settings.store)
        )

    async def async_configure(self, app_key: str, **changes) -> None:
        """Change AppSettings fields; empty strings and lists clear a field."""
        settings = self.settings.setdefault(app_key, AppSettings())
        for name, value in changes.items():
            setattr(settings, name, value if isinstance(value, bool) else value or None)
        if settings == AppSettings():
            self.settings.pop(app_key)
        await self.async_save()
        self._notify()

    def service_images(
        self, app_key: str, data: DockerData
    ) -> dict[str, tuple[str, bool]]:
        """Return {service: (running image, tracked)} of an app."""
        images = {c.service or c.name: c.image for c in data.app_containers(app_key)}
        tracked = self.get(app_key).services
        if tracked is not None:
            return {s: (i, s in tracked) for s, i in images.items()}
        return {
            s: (i, not any(hint in image_name(i) for hint in self.untracked))
            for s, i in images.items()
        }

    def history_versions(self, app_key: str) -> list[str]:
        return [h["version"] for h in self.get(app_key).history if h.get("version")]

    @property
    def host_network(self) -> bool:
        """True when Home Assistant runs with the host network."""
        host = (self.self_inspect or {}).get("HostConfig") or {}
        return host.get("NetworkMode") == "host"

    def self_service(self, data: DockerData | None, app_key: str) -> str | None:
        """Return the service of the Home Assistant container if it is in this app."""
        if data is None:
            return None
        return next(
            (
                c.service
                for c in data.app_containers(app_key)
                if c.id == self.self_container_id
            ),
            None,
        )

    # --- files ---

    def local_path(self, host_path: str) -> str:
        local = self.paths.to_local(host_path)
        if local is None:
            raise AppError(
                f"{host_path} is not mounted into the Home Assistant container. "
                "Add it as a volume with the same path to manage this app."
            )
        return local

    def can_edit(self, app: AppInfo) -> bool:
        return (
            bool(app.working_dir) and self.paths.to_local(app.working_dir) is not None
        )

    async def _read(self, host_path: str) -> str | None:
        return await self.hass.async_add_executor_job(
            read_text, self.local_path(host_path)
        )

    async def _write(self, host_path: str, content: str) -> None:
        await self.hass.async_add_executor_job(
            write_text, self.local_path(host_path), content, host_path.endswith(".env")
        )

    async def async_load_model(
        self, app: AppInfo, overrides: dict[str, str] | None = None
    ) -> ComposeModel:
        """Read and parse the compose project of an app (with optional new contents)."""
        if not app.config_files:
            raise AppError(f"{app.key} is not a compose project")
        overrides = overrides or {}
        env_file = f"{app.working_dir}/.env"

        def load() -> ComposeModel:
            contents = {}
            for file in app.config_files:
                text = overrides.get(file) or read_text(self.local_path(file))
                if text is None:
                    raise AppError(f"{file} not found")
                contents[file] = text
            env_text = overrides.get(env_file) or read_text(self.local_path(env_file))
            return load_model(
                app.project,
                app.working_dir,
                contents,
                env_text,
                env_file if env_text is not None else None,
                read_file=lambda p: read_text(self.local_path(p)),
            )

        return await self.hass.async_add_executor_job(load)

    # --- update checks ---

    async def async_check_updates(
        self, data: DockerData, catalog: dict[str, StoreApp]
    ) -> dict[str, AppUpdate]:
        results = {}
        # a copy: Managed can be switched while the registries are queried
        for app_key, settings in list(self.settings.items()):
            if not settings.managed or app_key not in data.apps:
                continue
            try:
                results[app_key] = await self._async_check_app(app_key, data, catalog)
            except (RegistryError, DockerError, OSError, ValueError) as err:
                _LOGGER.warning("Update check for %s failed: %s", app_key, err)
                results[app_key] = AppUpdate(app_key, None, None, error=str(err))
        return results

    async def _async_check_app(
        self, app_key: str, data: DockerData, catalog: dict[str, StoreApp]
    ) -> AppUpdate:
        settings = self.get(app_key)
        if store := settings.store:
            store_app = catalog.get(f"{store['repo_id']}.{store['app_id']}")
            installed = store.get("version") or store.get("package_version")
            latest = installed
            if store_app and store_app.is_newer_than(
                store.get("package_version"), store.get("content_hash")
            ):
                latest = store_app.version or store_app.package_version
                if latest == installed:
                    latest = f"{installed} (package {store_app.package_version})"
            return AppUpdate(app_key, installed, latest, store_app=store_app)

        services = []
        errors = []
        for service, (image, tracked) in self.service_images(app_key, data).items():
            try:
                result = await self._async_check_service(service, image, settings)
            except (RegistryError, DockerError, ValueError) as err:
                # one image that can't be checked doesn't hide the others
                _LOGGER.debug(
                    "Update check for %s/%s failed: %s", app_key, service, err
                )
                tag = split_image(image)[1] or "latest"
                result = ServiceUpdate(service, image, tag, tag, "tag")
                if tracked:
                    errors.append(f"{service}: {err}")
            result.tracked = tracked
            services.append(result)

        primary = primary_service(app_key, [s for s in services if s.tracked])
        update = AppUpdate(
            app_key,
            primary.current if primary else None,
            None,
            services,
            error="; ".join(errors) or None,
        )
        if primary and primary.has_update and settings.upstream:
            await self._async_apply_upstream(update, primary, settings)

        update.latest_version = update.installed_version
        if primary and primary.has_update:
            update.latest_version = primary.latest
        elif count := sum(s.tracked for s in update.targets):
            update.latest_version = (
                f"{update.installed_version} (+{count} image{'s' if count > 1 else ''})"
            )
        return update

    async def _async_apply_upstream(
        self, update: AppUpdate, primary: ServiceUpdate, settings: AppSettings
    ) -> None:
        """Use the upstream compose of the new version to find required services.

        Services whose image changes there are updated with the app. A database
        image that needs a new major version (or a minor one, unless allowed) puts
        the whole update on hold until it's upgraded by hand.
        """
        url = settings.upstream.replace("{version}", primary.latest)
        try:
            async with self.http.get(url) as resp:
                if resp.status >= 400:
                    raise AppError(f"HTTP {resp.status}")
                upstream = parse_services(await resp.text())
        except Exception as err:  # noqa: BLE001
            update.error = f"upstream compose {url}: {err}"
            return

        by_image = {
            image_name(str(spec.get("image", ""))): spec for spec in upstream.values()
        }
        blocked = []
        for service in update.services:
            if service is primary:
                continue
            spec = upstream.get(service.service) or by_image.get(
                image_name(service.image)
            )
            raw = str((spec or {}).get("image") or "")
            wanted = split_image(raw)[1] if raw and "$" not in raw else None
            if not wanted or wanted == service.current:
                continue
            if is_database_image(service.image) and not (
                settings.allow_db_minor and major(wanted) == major(service.current)
            ):
                blocked.append(
                    f"{service.service} needs {image_name(service.image)}:{wanted} "
                    f"(installed {service.current})"
                )
                continue
            service.latest = wanted
            service.required = True
        if blocked:
            update.blocked = (
                f"{primary.latest} requires a database upgrade: {'; '.join(blocked)}. "
                "Upgrade the database by hand first."
            )

    async def _async_check_service(
        self, service: str, image: str, settings: AppSettings
    ) -> ServiceUpdate:
        repository, tag, _ = split_image(image)
        tag = tag or "latest"
        if not is_floating(tag):
            tags = await self.registry.async_tags(image)
            latest = newest_tag(tag, tags, settings.include, settings.exclude)
            return ServiceUpdate(service, image, tag, latest or tag, "tag")

        local = await self.client.async_image_inspect(image)
        local_digests = [
            d.split("@", 1)[1]
            for d in (local or {}).get("RepoDigests") or []
            if "@" in d
        ]
        remote = await self.registry.async_digest(f"{repository}:{tag}")
        current = short_digest(local_digests[0]) if local_digests else tag
        if not remote or not local_digests or remote in local_digests:
            return ServiceUpdate(service, image, current, current, "digest")
        return ServiceUpdate(service, image, current, short_digest(remote), "digest")

    # --- operations ---

    @asynccontextmanager
    async def _operation(self, app_key: str, action: str):
        """One operation per app at a time, shown as busy, with Docker errors as AppError."""
        lock = self._locks.setdefault(app_key, asyncio.Lock())
        async with lock:
            self.busy[app_key] = action
            self.progress[app_key] = 0
            self._notify()
            try:
                yield
            except (ComposeError, DockerError) as err:
                raise AppError(str(err)) from err
            finally:
                self.busy.pop(app_key, None)
                self.progress.pop(app_key, None)
                self._notify()

    def _progress(self, app_key: str, start: float, end: float):
        def report(message: str, fraction: float | None):
            _LOGGER.debug("%s: %s", app_key, message)
            if fraction is not None:
                self.progress[app_key] = round(start + (end - start) * fraction, 1)
                self._notify()

        return report

    async def _async_apply(
        self,
        app_key: str,
        model: ComposeModel,
        data: DockerData,
        pull: bool,
        force: bool = False,
    ) -> None:
        """Pull (optional) and bring the project up; self-updates are handed off."""
        if pull:
            await self.engine.async_pull(model, progress=self._progress(app_key, 0, 60))
        ha_service = self.self_service(data, app_key)
        await self.engine.async_up(
            model,
            force_recreate=force,
            progress=self._progress(app_key, 60 if pull else 0, 100),
            skip={ha_service} if ha_service else None,
        )
        if ha_service:
            from ._selfupdate import async_start_self_update

            await async_start_self_update(self, model, ha_service)

    async def async_app_action(
        self, app_key: str, data: DockerData, action: str
    ) -> None:
        """Start, stop or restart all containers of an app."""
        containers = data.app_containers(app_key)
        if not containers:
            raise AppError(f"app {app_key} not found")
        if action == "stop" and self.self_service(data, app_key):
            raise AppError("Home Assistant can't stop its own app")
        await asyncio.gather(
            *(self.client.async_container_action(c.id, action) for c in containers)
        )

    async def async_recreate(self, app: AppInfo, data: DockerData, pull: bool) -> None:
        async with self._operation(app.key, "recreate"):
            model = await self.async_load_model(app)
            await self._async_apply(app.key, model, data, pull=pull, force=not pull)

    async def async_update(
        self, app: AppInfo, update: AppUpdate, data: DockerData
    ) -> None:
        """Install the pending update of a managed app as one package."""
        if update.store_app is not None:
            await self.async_install_store_app(
                update.store_app, app_key=app.key, is_update=True, data=data
            )
            return
        if update.blocked:
            raise AppError(update.blocked)
        if not update.targets:
            raise AppError("no update available")
        changes = {t.service: t.latest for t in update.targets if t.kind == "tag"}
        await self._async_change_tags(app, changes, update.installed_version, data)

    async def async_update_services(
        self, app: AppInfo, services: list[str], tag: str | None, data: DockerData
    ) -> dict[str, str]:
        """Update single services (also untracked ones) to a tag or their newest tag."""
        images = self.service_images(app.key, data)
        changes: dict[str, str] = {}
        for service in services:
            if service not in images:
                raise AppError(f"service {service} not found in {app.key}")
            if tag:
                changes[service] = tag
                continue
            result = await self._async_check_service(
                service, images[service][0], self.get(app.key)
            )
            if result.has_update:
                # a digest update keeps the tag; the pull brings the new image
                changes[service] = result.latest if result.kind == "tag" else ""
        if not changes:
            raise AppError("the services are up to date")
        await self._async_change_tags(
            app, {s: t for s, t in changes.items() if t}, None, data
        )
        return changes

    async def _async_change_tags(
        self,
        app: AppInfo,
        changes: dict[str, str],
        version: str | None,
        data: DockerData,
    ) -> None:
        """Edit image tags, back up, pull and up; roll back when containers fail."""
        async with self._operation(app.key, "update"):
            env_path = f"{app.working_dir}/.env"
            originals = {f: await self._read(f) for f in app.config_files}
            env_text = await self._read(env_path)

            working = {f: c for f, c in originals.items() if c is not None}
            env_now = env_text
            env_values: dict[str, tuple[str, str]] = {}
            for service, tag in changes.items():
                try:
                    edit = update_service_tag(
                        working,
                        env_path if env_text is not None else None,
                        env_now,
                        service,
                        tag,
                    )
                except ValueError as err:
                    raise AppError(str(err)) from err
                if edit.env_var:
                    name, value = edit.env_var
                    other = env_values.setdefault(name, (service, value))
                    if other[1] != value:
                        raise AppError(
                            f"{other[0]} and {service} share ${name} but need "
                            f"{other[1]} and {value}; update them by hand"
                        )
                working[edit.file] = edit.content
                env_now = edit.env_content or env_now

            changed = {f: c for f, c in working.items() if c != originals.get(f)}
            if env_now != env_text:
                changed[env_path] = env_now

            # parse before touching anything
            model = await self.async_load_model(app, overrides=changed)
            entry = await self._async_backup(
                app, [*app.config_files, env_path], version
            )
            for path, content in changed.items():
                await self._write(path, content)
            try:
                await self._async_apply(app.key, model, data, pull=True)
                if not self.self_service(data, app.key):
                    await self._async_wait_healthy(model.project)
            except (ComposeError, DockerError, AppError) as err:
                await self._async_restore(app, entry, data)
                raise AppError(f"update failed and was rolled back: {err}") from err
            _LOGGER.info("Updated %s: %s", app.key, changes)

    async def _async_wait_healthy(self, project: str) -> None:
        """Wait until every container of the project runs and is healthy."""
        loop = asyncio.get_running_loop()
        deadline = loop.time() + HEALTH_TIMEOUT
        while True:
            waiting = []
            for raw in await self.client.async_containers(all=True):
                labels = raw.get("Labels") or {}
                if labels.get(LABEL_PROJECT) != project:
                    continue
                service = labels.get(LABEL_SERVICE)
                status = raw.get("Status", "")
                health = health_from_status(status)
                if health == "unhealthy":
                    raise AppError(f"{service} is unhealthy")
                if raw.get("State") in ("exited", "dead") and "(0)" not in status:
                    raise AppError(f"{service} stopped: {status}")
                if raw.get("State") != "running" or health == "starting":
                    waiting.append(service)
            if not waiting:
                return
            if loop.time() > deadline:
                raise AppError(f"not healthy in time: {', '.join(waiting)}")
            await asyncio.sleep(5)

    async def _async_backup(
        self,
        app: AppInfo,
        paths: list[str],
        version: str | None,
        store: dict | None = None,
    ) -> dict:
        """Copy the files into the app history; keeps the newest HISTORY_KEEP."""
        stamp = datetime.now(UTC).strftime("%Y%m%d%H%M%S")
        safe = "".join(
            c if c.isalnum() or c in ".-_" else "_" for c in (version or "unknown")
        )
        folder = f"{app.working_dir}/{META_DIR}/history/{stamp}-{safe}"

        def backup() -> list[str]:
            return [
                path
                for path in paths
                if copy_file(
                    self.local_path(path),
                    self.local_path(f"{folder}/{PurePosixPath(path).name}"),
                )
            ]

        entry = {
            "version": version,
            "dir": folder,
            "time": datetime.now(UTC).isoformat(),
            "files": await self.hass.async_add_executor_job(backup),
            "store": store,
        }
        settings = self.settings.setdefault(app.key, AppSettings())
        settings.history.insert(0, entry)
        for old in settings.history[HISTORY_KEEP:]:
            if local := self.paths.to_local(old["dir"]):
                await self.hass.async_add_executor_job(remove_tree, local)
        settings.history = settings.history[:HISTORY_KEEP]
        await self.async_save()
        return entry

    async def _async_restore(self, app: AppInfo, entry: dict, data: DockerData) -> None:
        """Put back the files of a backup and bring the app up with them."""

        def restore():
            for path in entry["files"]:
                source = f"{entry['dir']}/{PurePosixPath(path).name}"
                copy_file(self.local_path(source), self.local_path(path))

        await self.hass.async_add_executor_job(restore)
        settings = self.get(app.key)
        if entry.get("store"):
            settings.store = entry["store"]
        settings.history = [h for h in settings.history if h is not entry]
        await self.async_save()
        await self._async_apply(app.key, await self.async_load_model(app), data, False)

    async def async_rollback(
        self, app: AppInfo, version: str, data: DockerData
    ) -> None:
        entry = next(
            (h for h in self.get(app.key).history if h.get("version") == version), None
        )
        if entry is None:
            raise AppError(f"no backup of version {version}")
        async with self._operation(app.key, "rollback"):
            await self._async_restore(app, entry, data)

    # --- settings ---

    def override_path(self, app: AppInfo) -> str | None:
        """The compose.override.yaml of an app, when the app uses one."""
        path = f"{app.working_dir}/{OVERRIDE_FILE}"
        return path if path in app.config_files else None

    async def async_environment(self, app: AppInfo) -> dict[str, dict[str, str | None]]:
        """Environment variables of each service, as compose resolves them."""
        model = await self.async_load_model(app)
        return {
            name: as_mapping(service.get("environment"))
            for name, service in model.services.items()
        }

    async def async_set_environment(
        self, app: AppInfo, service: str, values: dict[str, str], data: DockerData
    ) -> None:
        """Change environment variables in compose.override.yaml and recreate.

        The override keeps the change across package updates. Comments in it are
        not kept, except the header.
        """
        path = self.override_path(app)
        if path is None:
            raise AppError(f"{app.key} has no {OVERRIDE_FILE} to keep changes in")
        async with self._operation(app.key, "update"):
            original = await self._read(path)
            override = yaml.safe_load(original or "") or {}
            override["services"] = override.get("services") or {}
            spec = override["services"].get(service) or {}
            environment = as_mapping(spec.get("environment"))
            # a literal `$` would start a variable in compose
            environment.update(
                {key: str(value).replace("$", "$$") for key, value in values.items()}
            )
            spec["environment"] = environment
            override["services"][service] = spec
            content = OVERRIDE_HEADER + yaml.safe_dump(
                override, sort_keys=False, allow_unicode=True
            )
            model = await self.async_load_model(app, overrides={path: content})
            if service not in model.services:
                raise AppError(f"service {service} not found in {app.key}")
            await self._write(path, content)
            try:
                await self._async_apply(app.key, model, data, pull=False)
            except (ComposeError, DockerError, AppError):
                if original is not None:
                    await self._write(path, original)
                await self._async_apply(
                    app.key, await self.async_load_model(app), data, pull=False
                )
                raise

    # --- store ---

    def store_paths(self, app_key: str) -> tuple[str, str]:
        return f"{self.apps_root}/{app_key}", f"{self.data_root}/{app_key}"

    async def async_install_store_app(
        self,
        store_app: StoreApp,
        app_key: str | None = None,
        values: dict[str, str] | None = None,
        is_update: bool = False,
        data: DockerData | None = None,
    ) -> str:
        """Install a store app, or install the new package of an installed one."""
        app_key = safe_id(app_key or store_app.name)
        settings = self.settings.get(app_key)
        installed = settings.store if settings else None
        if is_update and installed is None:
            raise AppError(f"{app_key} was not installed from a store")
        if not is_update and (installed or (data and app_key in data.apps)):
            raise AppError(f"an app named {app_key} already exists")
        if store_app.compose is None:
            raise AppError(f"{store_app.name} has no compose file")

        app_dir, data_dir = self.store_paths(app_key)
        self.local_path(app_dir)  # fail early when not mounted
        compose, env = self._render(
            store_app,
            app_key,
            {**((installed or {}).get("values") or {}), **(values or {})},
        )
        if app_key in self.installing:
            raise AppError(f"{app_key} is being installed")
        if data is not None and not is_update:
            wanted = published_ports(interpolate(compose, env))
            if conflicts := await self._async_ports_in_use(wanted, data):
                raise AppError(
                    f"ports already in use: {', '.join(map(str, sorted(conflicts)))}. "
                    "Choose another port."
                )

        compose_path = f"{app_dir}/compose.yaml"
        override_path = f"{app_dir}/{OVERRIDE_FILE}"
        env_path = f"{app_dir}/.env"
        app = AppInfo(
            key=app_key,
            name=store_app.name,
            project=app_key,
            working_dir=app_dir,
            config_files=[compose_path, override_path],
        )

        async with self._operation(app_key, "update" if is_update else "install"):
            if installed:
                await self._async_backup(
                    app,
                    [compose_path, env_path],
                    installed.get("version"),
                    dict(installed),
                )
            else:
                self.installing[app_key] = store_app.id
            try:
                await self._write(compose_path, compose)
                await self._write(env_path, format_env(env))
                await self._write(
                    f"{app_dir}/{META_DIR}/manifest.json",
                    json.dumps(store_app.as_dict(with_compose=False), indent=2),
                )
                if await self._read(override_path) is None:
                    await self._write(
                        override_path, f"{OVERRIDE_HEADER}services: {{}}\n"
                    )
                model = await self.async_load_model(app)
                await self._async_apply(app_key, model, data, pull=True)
            except (AppError, ComposeError, DockerError, OSError):
                if not installed:
                    # a failed first install leaves nothing behind, so it can be retried
                    await self._async_discard_install(app_key, app_dir)
                raise
            finally:
                self.installing.pop(app_key, None)

            settings = self.settings.setdefault(app_key, AppSettings())
            settings.managed = True
            settings.store = {
                "repo_id": store_app.repo_id,
                "app_id": store_app.app_id,
                "name": store_app.name,
                "icon": store_app.icon,
                "version": store_app.version or store_app.package_version,
                "package_version": store_app.package_version,
                "content_hash": store_app.content_hash,
                "values": {v.key: env[v.key] for v in store_app.variables}
                | (values or {}),
                "main_service": store_app.main_service,
                "web_port": (values or {}).get("APP_PORT") or store_app.web_port,
                "web_path": store_app.web_path,
                "web_target": store_app.web_target,
                "web_scheme": store_app.web_scheme,
            }
            await self.async_save()
        return app_key

    def _render(
        self, store_app: StoreApp, app_key: str, values: dict[str, str]
    ) -> tuple[str, dict[str, str]]:
        return render_app(
            store_app,
            app_key,
            self.store_paths(app_key)[1],
            self.data_root,
            values,
            {
                "TZ": str(self.hass.config.time_zone or "UTC"),
                "PUID": "1000",
                "PGID": "1000",
            },
        )

    async def _async_ports_in_use(self, ports: set[int], data: DockerData) -> set[int]:
        """The ports that containers publish, or that the host already listens on."""
        used = {
            int(port.split(":", 1)[0])
            for container in data.containers.values()
            for port in container.ports
        }
        busy = ports & used
        if self.host_network:
            busy |= await self.hass.async_add_executor_job(
                host_ports_in_use, ports - used
            )
        return busy

    async def async_check_ports(
        self, store_app: StoreApp, data: DockerData, web_port: int | None = None
    ) -> dict:
        """Ports of a store app before installing: which are taken, and a free web port.

        The web port can be changed in the install dialog; other ports are fixed in
        the compose file, so a taken one blocks the install.
        """
        if store_app.compose is None:
            return {"busy": [], "web_port": None}
        web = web_port or store_app.web_port
        values = {"APP_PORT": str(web)} if web else {}
        compose, env = self._render(store_app, safe_id(store_app.name), values)
        wanted = published_ports(interpolate(compose, env))
        busy = await self._async_ports_in_use(wanted, data)
        free = web
        if web and web in busy:
            fixed = wanted - {web}
            for candidate in range(web + 1, min(web + 200, 65536)):
                if candidate not in fixed and not await self._async_ports_in_use(
                    {candidate}, data
                ):
                    free = candidate
                    break
        return {
            "busy": sorted(busy - {web} if free != web else busy),
            "web_port": free,
            "web_port_taken": bool(web and web in busy),
            "requested_web_port": web,
        }

    async def _async_discard_install(self, app_key: str, app_dir: str) -> None:
        """Remove the containers, networks and files of a failed first install."""
        try:
            project = ComposeModel(app_key, app_dir, [], None, {}, {}, {})
            await self.engine.async_down(project)
            await self.hass.async_add_executor_job(
                remove_tree, self.local_path(app_dir)
            )
        except (AppError, DockerError, OSError) as err:
            _LOGGER.warning("Clean-up after the failed install of %s: %s", app_key, err)

    async def async_uninstall(
        self, app: AppInfo, data: DockerData, remove_data: bool = False
    ) -> None:
        if self.self_service(data, app.key):
            raise AppError("Home Assistant can't remove its own app")
        remove_data = remove_data and bool(self.get(app.key).store)
        # check first, so nothing is removed when the data can't be deleted
        folders = (
            [self.local_path(f) for f in self.store_paths(app.key)]
            if remove_data
            else []
        )
        async with self._operation(app.key, "uninstall"):
            if app.is_compose and self.can_edit(app):
                await self.engine.async_down(
                    await self.async_load_model(app), remove_data
                )
            else:
                for container in data.app_containers(app.key):
                    await self.engine.async_remove(container.id, remove_data)
            for folder in folders:
                await self.hass.async_add_executor_job(remove_tree, folder)
            self.settings.pop(app.key, None)
            await self.async_save()
