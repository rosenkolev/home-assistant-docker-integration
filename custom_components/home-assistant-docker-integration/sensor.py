from homeassistant.components.sensor import DOMAIN as SENSOR_DOMAIN
from homeassistant.components.sensor import (
    SensorEntity,
    SensorEntityDescription,
)
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import EntityCategory
from homeassistant.core import HomeAssistant, ServiceCall, callback
from homeassistant.helpers.device_registry import DeviceEntryType, DeviceInfo
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback
from homeassistant.helpers.typing import StateType

from .const import _LOGGER, COORDINATOR, DOMAIN
from .coordinator import DockerContainerInfo, DockerDataUpdateCoordinator
from .entity import BaseDeviceEntity

# @dataclass(frozen=True, kw_only=True)
# class ContainerEntityDescription(SensorEntityDescription):
#     get_fn: Callable[[DockerContainerInfo], Any]


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
        key="images_total",
    ),
)

# CONTAINER_SENSOR_TYPES: tuple[ContainerEntityDescription, ...] = (
#     ContainerEntityDescription(key="status", get_fn=lambda x: x.status),
#     ContainerEntityDescription(key="health", get_fn=lambda x: x.health),
# )


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddConfigEntryEntitiesCallback,
) -> None:
    """Set up sensor platform."""

    coordinator: DockerDataUpdateCoordinator = hass.data[DOMAIN][COORDINATOR]

    async_add_entities(
        DockerDiagnosticSensor(coordinator, entity_description, entry.entry_id)
        for entity_description in DOCKER_SENSOR_TYPES
    )

    @callback
    def _add_container_entities() -> None:
        """Add Entities."""
        if coordinator.tracker.added_containers:
            async_add_entities(
                DockerContainerStatusSensor(coordinator, device_id)
                for device_id in coordinator.tracker.added_containers
            )

    # listen for new containers
    _add_container_entities()
    entry.async_on_unload(coordinator.async_add_listener(_add_container_entities))


# async def async_setup_platform(
#     hass: HomeAssistant, config, async_add_entities, discovery_info=None
# ):
#     sensor = MyDynamicSensor()
#     async_add_entities([sensor])

#     async def handle_next_state(call: ServiceCall):
#         await sensor.async_next_state()

#     hass.services.async_register(DOMAIN, "next_state", handle_next_state)


class DockerContainerStatusSensor(BaseDeviceEntity[DockerContainerInfo], SensorEntity):
    def __init__(
        self,
        coordinator: DockerDataUpdateCoordinator,
        device_id: str,
    ) -> None:
        dev = coordinator.data.containers.get(device_id)
        super().__init__(
            coordinator,
            device_id,
            key="containers",
            name=dev.name,
        )

        self._init_entity_id(SENSOR_DOMAIN)
        self._init_device_info(device_id, dev.name, dev.image_name)

    @property
    def native_value(self) -> str:
        """Return the value reported by the sensor."""
        return self.device.state

    @property
    def extra_state_attributes(self):
        dev = self.device
        return {
            "id": dev.id,
            "sid": dev.short_id,
            "status": dev.status,
            "ports": dev.ports,
            "project": dev.compose_project,
            "mounts": dev.mounts,
        }


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
