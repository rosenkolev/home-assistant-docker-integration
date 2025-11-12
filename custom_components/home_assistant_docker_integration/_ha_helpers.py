from typing import Callable

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import callback
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from .coordinator import DockerDataUpdateCoordinator, ServiceController


class DockerConfigEntry(ConfigEntry):
    runtime_data: ServiceController


@callback
def auto_add_containers_devices[TDevice](
    entry: DockerConfigEntry,
    async_add_entities: AddEntitiesCallback,
    create_fn: Callable[[str, DockerDataUpdateCoordinator], TDevice],
):

    coordinator = entry.runtime_data.data_coordinator

    @callback
    def _add_container_entities() -> None:
        """Add Entities."""
        if coordinator.tracker.added_containers:
            async_add_entities(
                create_fn(device_id, coordinator)
                for device_id in coordinator.tracker.added_containers
            )

    # listen for new containers
    _add_container_entities()
    entry.async_on_unload(coordinator.async_add_listener(_add_container_entities))
