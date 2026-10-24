"""App power switch: start or stop all containers of an app."""

from __future__ import annotations

from typing import Any

from homeassistant.components.switch import SwitchEntity
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback

from .coordinator import DockerAppsConfigEntry
from .entity import AppEntity, async_add_app_entities

PARALLEL_UPDATES = 1


async def async_setup_entry(
    hass: HomeAssistant,
    entry: DockerAppsConfigEntry,
    async_add_entities: AddConfigEntryEntitiesCallback,
) -> None:
    if entry.runtime_data.read_only:
        return
    async_add_app_entities(
        entry,
        async_add_entities,
        lambda key: [AppPowerSwitch(entry.runtime_data.docker, entry, key)],
    )


class AppPowerSwitch(AppEntity, SwitchEntity):
    def __init__(self, coordinator, entry, app_key):
        super().__init__(coordinator, entry, app_key, "power")

    @property
    def is_on(self) -> bool:
        return any(c.running for c in self.docker_data.app_containers(self.app_key))

    async def async_turn_on(self, **kwargs: Any) -> None:
        await self._async_app_action("start")

    async def async_turn_off(self, **kwargs: Any) -> None:
        await self._async_app_action("stop")
