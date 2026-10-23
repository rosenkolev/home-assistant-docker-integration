"""Docker Apps: monitor, manage and update Docker compose apps from Home Assistant."""

# cspell:words hassio webcomponent

from __future__ import annotations

from pathlib import Path

from homeassistant.components.http import StaticPathConfig
from homeassistant.components.frontend import add_extra_js_url, async_remove_panel
from homeassistant.components.panel_custom import async_register_panel
from homeassistant.const import Platform
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import ConfigEntryNotReady
from homeassistant.helpers import config_validation as cv
from homeassistant.helpers import device_registry as dr
from homeassistant.helpers import issue_registry as ir
from homeassistant.helpers.aiohttp_client import async_get_clientsession
from homeassistant.helpers.device_registry import DeviceEntryType
from homeassistant.helpers.typing import ConfigType

from ._docker_client import DockerClient, DockerError
from ._registry import RegistryClient
from .apps import AppManager
from .catalog import Catalog, Repository
from .const import (
    CONF_APPS_ROOT,
    CONF_DATA_ROOT,
    CONF_DOCKER_URL,
    CONF_READ_ONLY,
    CONF_REPO_KIND,
    CONF_REPO_URL,
    CONF_UNTRACKED,
    CONF_UPDATE_HOURS,
    DEFAULT_APPS_ROOT,
    DEFAULT_DATA_ROOT,
    DEFAULT_DOCKER_URL,
    DEFAULT_UPDATE_HOURS,
    DOMAIN,
    FRONTEND_URL,
    PANEL_URL,
    SUBENTRY_REPOSITORY,
)
from .coordinator import (
    DiskCoordinator,
    DockerAppsConfigEntry,
    DockerAppsRuntime,
    DockerCoordinator,
    StatsCoordinator,
    UpdatesCoordinator,
)
from .entity import app_identifier, async_remove_stale_devices, host_identifier
from .services import async_register_services
from .websocket import async_register_websocket

PLATFORMS = [Platform.BUTTON, Platform.SENSOR, Platform.SWITCH, Platform.UPDATE]
CONFIG_SCHEMA = cv.config_entry_only_config_schema(DOMAIN)
PANEL_VERSION = "15"


async def async_setup(hass: HomeAssistant, config: ConfigType) -> bool:
    await hass.http.async_register_static_paths(
        [StaticPathConfig(FRONTEND_URL, str(Path(__file__).parent / "www"), False)]
    )
    # dashboard card "Docker Apps": the app launcher
    add_extra_js_url(hass, f"{FRONTEND_URL}/docker-apps-cards.js?v={PANEL_VERSION}")
    async_register_websocket(hass)
    async_register_services(hass)
    return True


def _repositories(entry: DockerAppsConfigEntry) -> list[Repository]:
    return [
        Repository(
            url=s.data[CONF_REPO_URL],
            kind=s.data[CONF_REPO_KIND],
            name=s.data.get("name") or s.title,
        )
        for s in entry.subentries.values()
        if s.subentry_type == SUBENTRY_REPOSITORY
    ]


async def async_setup_entry(hass: HomeAssistant, entry: DockerAppsConfigEntry) -> bool:
    config = {**entry.data, **entry.options}
    client = DockerClient(config.get(CONF_DOCKER_URL, DEFAULT_DOCKER_URL))
    try:
        await client.async_connect()
    except DockerError as err:
        await client.async_close()
        raise ConfigEntryNotReady(f"Docker is not reachable: {err}") from err

    session = async_get_clientsession(hass)
    manager = AppManager(
        hass,
        entry.entry_id,
        client,
        RegistryClient(session),
        config.get(CONF_APPS_ROOT, DEFAULT_APPS_ROOT),
        config.get(CONF_DATA_ROOT, DEFAULT_DATA_ROOT),
        untracked=config.get(CONF_UNTRACKED),
        http=session,
    )
    await manager.async_setup()
    catalog = Catalog(hass, entry.entry_id, session)
    await catalog.async_load(_repositories(entry))

    docker = DockerCoordinator(hass, entry, client)
    runtime = DockerAppsRuntime(
        client=client,
        manager=manager,
        catalog=catalog,
        docker=docker,
        stats=StatsCoordinator(hass, entry, client, docker),
        disk=DiskCoordinator(hass, entry, client),
        updates=UpdatesCoordinator(
            hass,
            entry,
            manager,
            catalog,
            docker,
            config.get(CONF_UPDATE_HOURS, DEFAULT_UPDATE_HOURS),
        ),
        read_only=config.get(CONF_READ_ONLY, False),
    )
    entry.runtime_data = runtime

    try:
        await docker.async_config_entry_first_refresh()
    except ConfigEntryNotReady:
        await client.async_close()
        raise
    await runtime.stats.async_refresh()

    host = docker.data.host
    dr.async_get(hass).async_get_or_create(
        config_entry_id=entry.entry_id,
        identifiers={host_identifier(entry.entry_id)},
        entry_type=DeviceEntryType.SERVICE,
        manufacturer="Docker",
        model=host.os or "Docker Engine",
        name=f"Docker {host.name}",
        sw_version=host.version,
        hw_version=host.arch,
    )
    async_remove_stale_devices(hass, entry)

    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)

    entry.async_create_background_task(
        hass, docker.async_watch_events(), f"{DOMAIN} docker events"
    )
    entry.async_create_background_task(
        hass, runtime.disk.async_refresh(), f"{DOMAIN} disk"
    )
    entry.async_create_background_task(
        hass, runtime.updates.async_refresh(), f"{DOMAIN} updates"
    )
    entry.async_on_unload(entry.add_update_listener(_async_entry_updated))

    _check_issues(hass, manager)

    await async_register_panel(
        hass,
        frontend_url_path=PANEL_URL,
        webcomponent_name="docker-apps-panel",
        sidebar_title="Apps",
        sidebar_icon="mdi:docker",
        module_url=f"{FRONTEND_URL}/docker-apps-panel.js?v={PANEL_VERSION}",
        require_admin=True,
    )
    return True


async def _async_entry_updated(
    hass: HomeAssistant, entry: DockerAppsConfigEntry
) -> None:
    """Repositories (subentries) changed: reload the catalog without a full reload."""
    runtime = entry.runtime_data
    repositories = _repositories(entry)
    if {r.id for r in repositories} != set(runtime.catalog.repositories):
        await runtime.catalog.async_load(repositories)
        hass.async_create_task(runtime.updates.async_check_now(refresh_catalog=True))


def _check_issues(hass: HomeAssistant, manager: AppManager) -> None:
    for issue_id, path in (
        ("apps_root_not_mounted", manager.apps_root),
        ("data_root_not_mounted", manager.data_root),
    ):
        if manager.self_container_id and manager.paths.to_local(path) is None:
            ir.async_create_issue(
                hass,
                DOMAIN,
                issue_id,
                is_fixable=False,
                severity=ir.IssueSeverity.WARNING,
                translation_key=issue_id,
                translation_placeholders={"path": path},
            )
        else:
            ir.async_delete_issue(hass, DOMAIN, issue_id)

    if "hassio" in hass.config.components:
        ir.async_create_issue(
            hass,
            DOMAIN,
            "supervised",
            is_fixable=False,
            severity=ir.IssueSeverity.WARNING,
            translation_key="supervised",
        )


async def async_unload_entry(hass: HomeAssistant, entry: DockerAppsConfigEntry) -> bool:
    unloaded = await hass.config_entries.async_unload_platforms(entry, PLATFORMS)
    if unloaded:
        async_remove_panel(hass, PANEL_URL, warn_if_unknown=False)
        await entry.runtime_data.client.async_close()
    return unloaded


async def async_remove_config_entry_device(
    hass: HomeAssistant, entry: DockerAppsConfigEntry, device: dr.DeviceEntry
) -> bool:
    """Allow removing the device of an app that no longer exists."""
    if host_identifier(entry.entry_id) in device.identifiers:
        return False
    data = entry.runtime_data.docker.data
    return not any(
        app_identifier(entry.entry_id, key) in device.identifiers for key in data.apps
    )
