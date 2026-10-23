"""App restart button."""

from __future__ import annotations

from homeassistant.components.button import ButtonDeviceClass, ButtonEntity
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback

from ._docker_client import DockerError
from .apps import AppError
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
        lambda key: [AppRestartButton(entry.runtime_data.docker, entry, key)],
    )


class AppRestartButton(AppEntity, ButtonEntity):
    _attr_device_class = ButtonDeviceClass.RESTART

    def __init__(self, coordinator, entry, app_key):
        super().__init__(coordinator, entry, app_key, "restart")

    async def async_press(self) -> None:
        try:
            await self.runtime.manager.async_app_action(
                self.app_key, self.docker_data, "restart"
            )
        except (AppError, DockerError) as err:
            raise HomeAssistantError(str(err)) from err
        await self.coordinator.async_refresh()
