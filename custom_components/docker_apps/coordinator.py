"""Coordinators: Docker state (events + reconcile), stats, disk usage and updates."""

from __future__ import annotations

import asyncio
from dataclasses import dataclass
from datetime import timedelta

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers import issue_registry as ir
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator, UpdateFailed

from ._docker_client import DockerClient, DockerError, container_key, inspect_details
from ._models import LABEL_HELPER, LABEL_SERVICE, ContainerStats, DiskUsage, DockerData
from .apps import AppManager, AppUpdate
from .catalog import Catalog
from .const import _LOGGER, DOMAIN, EVENT_DOCKER

RECONCILE_INTERVAL = timedelta(seconds=60)
STATS_INTERVAL = timedelta(seconds=30)
DISK_INTERVAL = timedelta(hours=1)
FORWARDED_EVENTS = {
    "start",
    "die",
    "oom",
    "restart",
    "health_status: healthy",
    "health_status: unhealthy",
}


@dataclass
class DockerAppsRuntime:
    client: DockerClient
    manager: AppManager
    catalog: Catalog
    docker: DockerCoordinator
    stats: StatsCoordinator
    disk: DiskCoordinator
    updates: UpdatesCoordinator
    read_only: bool = False


type DockerAppsConfigEntry = ConfigEntry[DockerAppsRuntime]


class DockerCoordinator(DataUpdateCoordinator[DockerData]):
    """Containers grouped into apps; refreshed by Docker events and every minute."""

    def __init__(self, hass: HomeAssistant, entry: ConfigEntry, client: DockerClient):
        super().__init__(
            hass,
            _LOGGER,
            config_entry=entry,
            name=f"{DOMAIN}_docker",
            update_interval=RECONCILE_INTERVAL,
        )
        self.client = client
        # container id -> inspect details; dropped when an event touches the container
        self._details: dict[str, dict] = {}

    async def _async_update_data(self) -> DockerData:
        try:
            data = await self.client.async_fetch()
            missing = [c for c in data.containers.values() if c.id not in self._details]
            inspects = await asyncio.gather(
                *(self.client.async_container_inspect(c.id) for c in missing),
                return_exceptions=True,
            )
        except DockerError as err:
            raise UpdateFailed(f"Docker is not reachable: {err}") from err
        for container, inspect in zip(missing, inspects, strict=True):
            if isinstance(inspect, dict):
                self._details[container.id] = inspect_details(inspect)
        ids = {c.id for c in data.containers.values()}
        self._details = {k: v for k, v in self._details.items() if k in ids}
        for container in data.containers.values():
            for name, value in self._details.get(container.id, {}).items():
                setattr(container, name, value)
        return data

    async def async_watch_events(self) -> None:
        """Listen to Docker events forever, reconnecting with a back-off."""
        delay = 5
        while True:
            try:
                async for event in self.client.async_events({"type": ["container"]}):
                    delay = 5
                    self._handle_event(event)
            except asyncio.CancelledError:
                raise
            except (DockerError, OSError, ValueError) as err:
                _LOGGER.debug("Docker event stream closed: %s", err)
            await asyncio.sleep(delay)
            delay = min(delay * 2, 300)

    def _handle_event(self, event: dict) -> None:
        action = event.get("Action") or event.get("status") or ""
        attributes = (event.get("Actor") or {}).get("Attributes") or {}
        if attributes.get(LABEL_HELPER):
            return
        self._details.pop((event.get("Actor") or {}).get("ID", ""), None)
        if action in FORWARDED_EVENTS:
            name = attributes.get("name", "")
            key, app = container_key(attributes, name)
            self.hass.bus.async_fire(
                EVENT_DOCKER,
                {
                    "app": app,
                    "container": key,
                    "name": name,
                    "service": attributes.get(LABEL_SERVICE),
                    "action": action.replace("health_status: ", ""),
                    "type": "health" if action.startswith("health_status") else "state",
                    "exit_code": attributes.get("exitCode"),
                },
            )
        if not action.startswith(("exec_", "top", "attach", "resize")):
            self.hass.async_create_task(self.async_request_refresh())


class StatsCoordinator(DataUpdateCoordinator[dict[str, ContainerStats]]):
    def __init__(self, hass, entry, client: DockerClient, docker: DockerCoordinator):
        super().__init__(
            hass,
            _LOGGER,
            config_entry=entry,
            name=f"{DOMAIN}_stats",
            update_interval=STATS_INTERVAL,
        )
        self.client = client
        self.docker = docker
        self._semaphore = asyncio.Semaphore(8)

    async def _async_update_data(self) -> dict[str, ContainerStats]:
        data = self.docker.data
        if data is None:
            return {}
        running = [c for c in data.containers.values() if c.running]

        async def one(container):
            async with self._semaphore:
                try:
                    return container.key, await self.client.async_container_stats(
                        container.id
                    )
                except DockerError:
                    return container.key, None

        results = await asyncio.gather(*(one(c) for c in running))
        return {key: stats for key, stats in results if stats is not None}


class DiskCoordinator(DataUpdateCoordinator[DiskUsage]):
    def __init__(self, hass, entry, client: DockerClient):
        super().__init__(
            hass,
            _LOGGER,
            config_entry=entry,
            name=f"{DOMAIN}_disk",
            update_interval=DISK_INTERVAL,
        )
        self.client = client

    async def _async_update_data(self) -> DiskUsage:
        try:
            return await self.client.async_disk_usage()
        except DockerError as err:
            raise UpdateFailed(str(err)) from err


class UpdatesCoordinator(DataUpdateCoordinator[dict[str, AppUpdate]]):
    """Refresh repositories and check managed apps for updates."""

    def __init__(
        self,
        hass,
        entry,
        manager: AppManager,
        catalog: Catalog,
        docker: DockerCoordinator,
        hours: int,
    ):
        super().__init__(
            hass,
            _LOGGER,
            config_entry=entry,
            name=f"{DOMAIN}_updates",
            update_interval=timedelta(hours=hours),
        )
        self.manager = manager
        self.catalog = catalog
        self.docker = docker
        self.refresh_catalog = True

    async def _async_update_data(self) -> dict[str, AppUpdate]:
        if self.refresh_catalog:
            await self.catalog.async_refresh()
        self.refresh_catalog = True
        if self.docker.data is None:
            return {}
        results = await self.manager.async_check_updates(
            self.docker.data, self.catalog.apps
        )
        self._sync_hold_issues(results)
        return results

    def _sync_hold_issues(self, results: dict[str, AppUpdate]) -> None:
        """One repair issue per app whose update waits for a manual database upgrade."""
        for app_key, update in results.items():
            issue_id = f"update_on_hold_{app_key}"
            if update.blocked:
                ir.async_create_issue(
                    self.hass,
                    DOMAIN,
                    issue_id,
                    is_fixable=False,
                    severity=ir.IssueSeverity.WARNING,
                    translation_key="update_on_hold",
                    translation_placeholders={"app": app_key, "reason": update.blocked},
                )
            else:
                ir.async_delete_issue(self.hass, DOMAIN, issue_id)

    async def async_check_now(self, refresh_catalog: bool = False) -> None:
        self.refresh_catalog = refresh_catalog
        if refresh_catalog:
            self.manager.registry.clear_cache()
        await self.async_refresh()
