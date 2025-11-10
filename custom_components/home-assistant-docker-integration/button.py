from homeassistant.components.button import DOMAIN as BUTTON_DOMAIN
from homeassistant.components.button import ButtonDeviceClass, ButtonEntity
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import EntityCategory
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from .const import COORDINATOR, DOMAIN
from .coordinator import DockerContainerInfo, DockerDataUpdateCoordinator
from .entity import BaseDeviceEntity


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up button platform."""

    coordinator: DockerDataUpdateCoordinator = hass.data[DOMAIN][COORDINATOR]

    @callback
    def _add_container_entities() -> None:
        """Add Entities."""
        if coordinator.tracker.added_containers:
            async_add_entities(
                ContainerRestartButton(coordinator, device_id)
                for device_id in coordinator.tracker.added_containers
            )

    # listen for new containers
    _add_container_entities()
    entry.async_on_unload(coordinator.async_add_listener(_add_container_entities))


class ContainerRestartButton(BaseDeviceEntity[DockerContainerInfo], ButtonEntity):
    _attr_device_class = ButtonDeviceClass.RESTART
    _attr_entity_category = EntityCategory.CONFIG

    def __init__(
        self,
        coordinator: DockerDataUpdateCoordinator,
        device_id: str,
    ) -> None:
        """Initialize the container."""
        dev = coordinator.data.containers.get(device_id)
        super().__init__(
            coordinator,
            device_id,
            name=dev.name,
            key="containers",
            sub_name="restart",
        )

        self._init_entity_id(BUTTON_DOMAIN)
        self._init_device_info(device_id, dev.name, model_id=dev.image_name)

    async def async_press(self) -> None:
        """Handle the button press."""
        await self.coordinator.api.async_container_restart(self._id)
