from dataclasses import dataclass
from typing import Any, Callable

from homeassistant.components.sensor import (
    DOMAIN as SENSOR_DOMAIN,
)
from homeassistant.components.sensor import (
    SensorDeviceClass,
    SensorEntity,
    SensorEntityDescription,
    SensorStateClass,
)
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import EntityCategory
from homeassistant.core import HomeAssistant, ServiceCall, callback
from homeassistant.helpers.device_registry import DeviceEntryType, DeviceInfo
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback
from homeassistant.helpers.typing import StateType

from .const import COORDINATOR, DOMAIN
from .coordinator import DockerContainerInfo, DockerDataUpdateCoordinator
from .entity import ContainerEntity


@dataclass(frozen=True, kw_only=True)
class ContainerEntityDescription(SensorEntityDescription):
    get_fn: Callable[[DockerContainerInfo], Any]


DOCKER_SENSOR_TYPES: tuple[SensorEntityDescription, ...] = (
    SensorEntityDescription(
        name="Docker Containers",
        key="containers_total",
    ),
    SensorEntityDescription(
        name="Docker Running Containers",
        key="containers_running",
    ),
    SensorEntityDescription(
        name="Docker Images",
        key="images",
    ),
)

CONTAINER_SENSOR_TYPES: tuple[ContainerEntityDescription, ...] = (
    # ContainerEntityDescription(key="status", get_fn=lambda x: x.status),
    ContainerEntityDescription(key="health", get_fn=lambda x: x.health),
)


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddConfigEntryEntitiesCallback,
) -> None:
    """Set up sensor platform."""

    coordinator = hass.data[DOMAIN][COORDINATOR]

    async_add_entities(
        DockerDiagnosticSensor(coordinator, entity_description, entry.entry_id)
        for entity_description in DOCKER_SENSOR_TYPES
    )

    @callback
    def _add_container_entities() -> None:
        """Add Entities."""
        if not coordinator.new_device_ids:
            return

        async_add_entities(
            DockerContainerSensor(coordinator, device_id, entity_description)
            for device_id in coordinator.new_device_ids
            for entity_description in CONTAINER_SENSOR_TYPES
        )

    _add_container_entities()

    # listen for new containers
    entry.async_on_unload(coordinator.async_add_listener(_add_container_entities))


# async def async_setup_platform(
#     hass: HomeAssistant, config, async_add_entities, discovery_info=None
# ):
#     sensor = MyDynamicSensor()
#     async_add_entities([sensor])

#     async def handle_next_state(call: ServiceCall):
#         await sensor.async_next_state()

#     hass.services.async_register(DOMAIN, "next_state", handle_next_state)


class DockerContainerSensor(ContainerEntity, SensorEntity):
    _attr_has_entity_name = True
    _attr_entity_category = EntityCategory.DIAGNOSTIC

    def __init__(
        self,
        coordinator: DockerDataUpdateCoordinator,
        device_id: str,
        description: ContainerEntityDescription,
    ) -> None:
        super().__init__(coordinator, device_id, description.key)
        self._attr_name = f"{self.device.name} {description.key}"
        self.entity_description = description
        self.entity_id = f"{SENSOR_DOMAIN}.{self._attr_unique_id}"

    @property
    def native_value(self) -> int | float:
        """Return the value reported by the sensor."""
        return self.entity_description.get_fn(self.device)

    @property
    def extra_state_attributes(self):
        dev = self.device
        return {"id": dev.id, "sid": dev.short_id}


class DockerDiagnosticSensor(SensorEntity):
    """Representation of a Sensor."""

    _attr_has_entity_name = True
    _attr_entity_category = EntityCategory.DIAGNOSTIC

    def __init__(
        self,
        coordinator: DockerDataUpdateCoordinator,
        entity_description: SensorEntityDescription,
        entry_id: str,
    ) -> None:
        """Initiate Sun Sensor."""
        self.entity_description = entity_description
        self._attr_unique_id = f"{DOMAIN}_host_{entity_description.key}"
        self.entity_id = f"{SENSOR_DOMAIN}.{self._attr_unique_id}"
        self._coordinator = coordinator
        self._attr_device_info = DeviceInfo(
            name="Docker Host",
            identifiers={(DOMAIN, entry_id)},
            entry_type=DeviceEntryType.SERVICE,
        )

    @property
    def available(self) -> bool:
        """Return if entity is available."""
        return self._coordinator.last_update_success

    @property
    def native_value(self) -> StateType:
        """Return value of sensor."""
        return getattr(self._coordinator.data, self.entity_description.key)

    async def async_update(self) -> None:
        """Update the entity."""
        if self.enabled:
            await self._coordinator.async_request_refresh()
