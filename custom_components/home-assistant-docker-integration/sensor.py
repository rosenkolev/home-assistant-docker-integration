from homeassistant.components.sensor import (
    SensorDeviceClass,
    SensorEntity,
    SensorEntityDescription,
    SensorStateClass,
)
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import PERCENTAGE, EntityCategory
from homeassistant.core import HomeAssistant, ServiceCall, callback
from homeassistant.helpers.device_registry import DeviceEntryType, DeviceInfo
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback
from homeassistant.helpers.typing import StateType

from .const import _LOGGER, COORDINATOR, DOMAIN
from .coordinator import DockerDataUpdateCoordinator
from .entity import ContainerEntity

DOCKER_SENSOR_TYPES: tuple[SensorEntityDescription, ...] = (
    SensorEntityDescription(
        name="Docker Containers"
        key="containers_total",
    ),
    SensorEntityDescription(
        name="Docker Running Containers"
        key="containers_running",
    ),
    SensorEntityDescription(
        name="Docker Images"
        key="images",
    ),
    # SensorEntityDescription(
    #     key="images",
    #     device_class=SensorDeviceClass.POWER_FACTOR,
    #     native_unit_of_measurement=PERCENTAGE,
    #     state_class=SensorStateClass.MEASUREMENT,
    # ),
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
        if not coordinator.new_containers:
            return

        async_add_entities(
            DockerContainerSensor(coordinator, device_id, entry.entry_id)
            for device_id in coordinator.new_containers
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
    def __init__(
        self,
        coordinator: DockerDataUpdateCoordinator,
        device_id: str,
        entry_id: str,
    ) -> None:
        super().__init__(coordinator, device_id)
        self._attr_name=f"Container {self.device.name} status"
        self._attr_unique_id = f"{entry_id}_{device_id}_status"

    @property
    def native_value(self) -> int | float:
        """Return the value reported by the sensor."""
        return self.device.status
    
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
        self.entity_id = f"{DOMAIN}.{entry_id}_{entity_description.key}"

        self._attr_unique_id = f"{entry_id}-{entity_description.key}"
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
