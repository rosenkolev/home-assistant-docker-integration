"""Update entity per managed app: one package update for all its containers."""

from __future__ import annotations

from typing import Any

from homeassistant.components.update import UpdateEntity, UpdateEntityFeature
from homeassistant.const import EntityCategory
from homeassistant.core import HomeAssistant, callback
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers import entity_registry as er
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback

from ._docker_client import DockerError
from .apps import AppError, AppUpdate
from .const import DOMAIN
from .coordinator import DockerAppsConfigEntry
from .entity import AppEntity, app_unique_id

PARALLEL_UPDATES = 1


async def async_setup_entry(
    hass: HomeAssistant,
    entry: DockerAppsConfigEntry,
    async_add_entities: AddConfigEntryEntitiesCallback,
) -> None:
    """Keep one update entity per managed app, also when Managed is switched."""
    runtime = entry.runtime_data
    added: set[str] = set()

    @callback
    def sync() -> None:
        # apps vanish for a moment while they are recreated, so only Managed decides
        managed = {k for k, s in runtime.manager.settings.items() if s.managed}
        new = (managed & runtime.docker.data.apps.keys()) - added
        async_add_entities(AppUpdateEntity(runtime.updates, entry, k) for k in new)
        registry = er.async_get(hass)
        for key in added - managed:
            unique_id = app_unique_id(entry.entry_id, key, "update")
            if entity_id := registry.async_get_entity_id("update", DOMAIN, unique_id):
                registry.async_remove(entity_id)
        added.intersection_update(managed)
        added.update(new)

    sync()
    entry.async_on_unload(runtime.manager.add_listener(sync))
    entry.async_on_unload(runtime.docker.async_add_listener(sync))
    runtime.app_removed.append(added.discard)


class AppUpdateEntity(AppEntity, UpdateEntity):
    _attr_entity_category = EntityCategory.CONFIG

    def __init__(self, coordinator, entry, app_key):
        super().__init__(coordinator, entry, app_key, "update")
        self._attr_supported_features = UpdateEntityFeature.RELEASE_NOTES
        if not self.runtime.read_only:
            self._attr_supported_features |= (
                UpdateEntityFeature.INSTALL
                | UpdateEntityFeature.PROGRESS
                | UpdateEntityFeature.SPECIFIC_VERSION
            )

    async def async_added_to_hass(self) -> None:
        await super().async_added_to_hass()
        self.async_on_remove(
            self.runtime.manager.add_listener(self.async_write_ha_state)
        )

    @property
    def _update(self) -> AppUpdate | None:
        return (self.coordinator.data or {}).get(self.app_key)

    @property
    def title(self) -> str | None:
        update = self._update
        return update.store_app.name if update and update.store_app else self.app_key

    @property
    def installed_version(self) -> str | None:
        return self._update.installed_version if self._update else None

    @property
    def latest_version(self) -> str | None:
        update = self._update
        if update is None:
            return None
        return update.latest_version if update.has_update else update.installed_version

    @property
    def release_summary(self) -> str | None:
        update = self._update
        if update and update.error:
            return f"Update check failed: {update.error}"[:255]
        return update.summary()[:255] if update and update.has_update else None

    @property
    def release_url(self) -> str | None:
        update = self._update
        if update and update.store_app:
            return update.store_app.source or update.store_app.website
        return None

    @property
    def in_progress(self) -> bool:
        return self.app_key in self.runtime.manager.busy

    @property
    def update_percentage(self) -> float | None:
        return self.runtime.manager.progress.get(self.app_key)

    def version_is_newer(self, latest_version: str, installed_version: str) -> bool:
        # versions can carry notes like "v2.1.0 (+1 image)"; the check already knows
        return bool(self._update and self._update.has_update)

    async def async_release_notes(self) -> str | None:
        update = self._update
        if update is None:
            return None
        lines = []
        if update.store_app is not None:
            lines.append(f"**{update.store_app.name} {update.latest_version}**")
            if update.store_app.release_notes:
                lines += ["", update.store_app.release_notes]
        if update.blocked:
            lines += [f"**On hold:** {update.blocked}", ""]
        if update.targets:
            lines += ["| Service | Installed | New |", "| --- | --- | --- |"]
            lines += [
                f"| {s.service}{' (required)' if s.required else ''} | {s.current} | {s.latest} |"
                for s in update.targets
            ]
        untracked = [
            f"{s.service} ({s.current}{f', {s.latest} available' if s.has_update else ''})"
            for s in update.kept
        ]
        if untracked:
            lines += ["", "Not tracked, kept as they are: " + ", ".join(untracked)]
        if history := self.runtime.manager.history_versions(self.app_key):
            lines += ["", f"Rollback available to: {', '.join(history)}"]
        return "\n".join(lines) or None

    async def async_install(
        self, version: str | None, backup: bool, **kwargs: Any
    ) -> None:
        manager = self.runtime.manager
        history = manager.history_versions(self.app_key)
        if self.app is None:
            raise HomeAssistantError(f"app {self.app_key} is not running on Docker")
        try:
            if version and version != self.latest_version:
                if version not in history:
                    raise HomeAssistantError(
                        f"version {version} can't be installed; available for rollback: "
                        f"{', '.join(history) or 'none'}"
                    )
                await manager.async_rollback(self.app, version, self.docker_data)
            elif self._update and self._update.has_update:
                await manager.async_update(self.app, self._update, self.docker_data)
            else:
                raise HomeAssistantError("no update available")
        except (AppError, DockerError) as err:
            raise HomeAssistantError(str(err)) from err
        await self.runtime.docker.async_refresh()
        await self.coordinator.async_check_now()
