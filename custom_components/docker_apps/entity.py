"""Base entities: one device per Docker host and one per app (no device per container)."""

from __future__ import annotations

from collections.abc import Callable
from urllib.parse import urlparse

from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers import device_registry as dr
from homeassistant.helpers.device_registry import DeviceInfo
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback
from homeassistant.helpers.network import NoURLAvailableError, get_url
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from ._models import AppInfo, DockerData
from .const import DOMAIN
from .coordinator import DockerAppsConfigEntry, DockerAppsRuntime


def host_identifier(entry_id: str) -> tuple[str, str]:
    return (DOMAIN, entry_id)


def app_identifier(entry_id: str, app_key: str) -> tuple[str, str]:
    return (DOMAIN, f"{entry_id}_{app_key}")


def app_web(runtime: DockerAppsRuntime, app: AppInfo) -> tuple[str, str, str] | None:
    """Port, path and scheme of the app web UI.

    For a store app, the host port published for its web container port; else the
    store port, else the first published TCP port.
    """
    store = runtime.manager.get(app.key).store or {}
    target, scheme = store.get("web_target"), store.get("web_scheme")
    if store and "web_target" not in store:
        # installed before the web port was stored: take it from the catalog
        if listed := runtime.catalog.apps.get(f"{store['repo_id']}.{store['app_id']}"):
            target, scheme = listed.web_target, listed.web_scheme
    mappings = [
        mapping.removesuffix("/tcp").split(":", 1)
        for container in runtime.docker.data.app_containers(app.key)
        for mapping in container.ports
        if mapping.endswith("/tcp")
    ]
    port = next(
        (host for host, inner in mappings if target and inner == str(target)), None
    )
    port = port or store.get("web_port") or next((host for host, _ in mappings), None)
    if not port:
        return None
    return str(port), store.get("web_path") or "/", scheme or "http"


def app_web_url(
    hass: HomeAssistant, entry: DockerAppsConfigEntry, app: AppInfo
) -> str | None:
    """Web UI on the host Home Assistant is reached at (for the device Visit link).

    The panel builds its own link from the browser address instead, because the
    address Home Assistant knows itself by can be a Docker-internal one.
    """
    if (web := app_web(entry.runtime_data, app)) is None:
        return None
    try:
        host = urlparse(get_url(hass, allow_external=False)).hostname
    except NoURLAvailableError:
        host = None
    return f"{web[2]}://{host or 'localhost'}:{web[0]}{web[1]}"


def app_device_info(
    hass: HomeAssistant, entry: DockerAppsConfigEntry, app: AppInfo
) -> DeviceInfo:
    runtime = entry.runtime_data
    store = runtime.manager.get(app.key).store or {}
    containers = runtime.docker.data.app_containers(app.key)
    main = next(
        (c for c in containers if c.service == store.get("main_service")),
        containers[0] if containers else None,
    )
    info = DeviceInfo(
        identifiers={app_identifier(entry.entry_id, app.key)},
        name=store.get("name") or app.name,
        manufacturer="Docker Apps",
        model="Compose app" if app.is_compose else "Container",
        sw_version=store.get("version") or (main.image_version if main else None),
        configuration_url=app_web_url(hass, entry, app),
    )
    host = dr.async_get(hass).async_get_device_by_identifier(
        host_identifier(entry.entry_id), entry.entry_id
    )
    if host is not None:
        info["via_device_id"] = host.id
    return info


@callback
def async_remove_stale_devices(
    hass: HomeAssistant, entry: DockerAppsConfigEntry
) -> None:
    """Remove the devices (and their entities) of apps that no longer exist."""
    registry = dr.async_get(hass)
    keep = {host_identifier(entry.entry_id)} | {
        app_identifier(entry.entry_id, key)
        for key in entry.runtime_data.docker.data.apps
    }
    for device in dr.async_entries_for_config_entry(registry, entry.entry_id):
        if not device.identifiers & keep:
            registry.async_remove_device(device.id)


class HostEntity(CoordinatorEntity):
    _attr_has_entity_name = True

    def __init__(self, coordinator, entry: DockerAppsConfigEntry, key: str):
        super().__init__(coordinator)
        self._attr_unique_id = f"{entry.entry_id}_host_{key}"
        self._attr_translation_key = key
        self._attr_device_info = DeviceInfo(
            identifiers={host_identifier(entry.entry_id)}
        )


class AppEntity(CoordinatorEntity):
    """Entity of one app, on the app device."""

    _attr_has_entity_name = True

    def __init__(
        self, coordinator, entry: DockerAppsConfigEntry, app_key: str, key: str
    ):
        super().__init__(coordinator)
        self.runtime: DockerAppsRuntime = entry.runtime_data
        self.app_key = app_key
        self._attr_unique_id = f"{entry.entry_id}_{app_key}_{key}"
        self._attr_translation_key = key
        app = self.runtime.docker.data.apps[app_key]
        self._attr_device_info = app_device_info(coordinator.hass, entry, app)

    @property
    def docker_data(self) -> DockerData:
        return self.runtime.docker.data

    @property
    def app(self) -> AppInfo | None:
        return self.docker_data.apps.get(self.app_key)

    @property
    def available(self) -> bool:
        return self.runtime.docker.last_update_success and self.app is not None


@callback
def async_add_app_entities(
    entry: DockerAppsConfigEntry,
    async_add_entities: AddConfigEntryEntitiesCallback,
    factory: Callable[[str], list],
) -> None:
    """Add entities for every app now and whenever a new app shows up."""
    added: set[str] = set()
    docker = entry.runtime_data.docker

    @callback
    def add_new() -> None:
        new = docker.data.apps.keys() - added
        added.update(new)
        if entities := [e for key in new for e in factory(key)]:
            async_add_entities(entities)

    add_new()
    entry.async_on_unload(docker.async_add_listener(add_new))
