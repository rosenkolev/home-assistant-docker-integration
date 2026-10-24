"""Sensors: app status, CPU and memory; host containers, images and disk."""

# cspell:words MEBIBYTES

from __future__ import annotations

from homeassistant.components.sensor import (
    SensorDeviceClass,
    SensorEntity,
    SensorStateClass,
)
from homeassistant.const import PERCENTAGE, EntityCategory, UnitOfInformation
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback

from .coordinator import DockerAppsConfigEntry
from .entity import AppEntity, HostEntity, app_web, async_add_app_entities

PARALLEL_UPDATES = 0

APP_STATES = ["running", "partial", "starting", "unhealthy", "stopped", "updating"]


def app_status(containers, busy: str | None) -> str:
    if busy:
        return "updating"
    if not containers:
        return "stopped"
    running = [c for c in containers if c.running]
    if not running:
        return "stopped"
    if any(c.health == "unhealthy" for c in running):
        return "unhealthy"
    if any(c.health == "starting" for c in running):
        return "starting"
    if len(running) < len(containers):
        return "partial"
    return "running"


async def async_setup_entry(
    hass: HomeAssistant,
    entry: DockerAppsConfigEntry,
    async_add_entities: AddConfigEntryEntitiesCallback,
) -> None:
    runtime = entry.runtime_data
    async_add_entities(
        [
            HostContainersSensor(runtime.docker, entry),
            HostImagesSensor(runtime.docker, entry),
            HostReclaimableSensor(runtime.disk, entry),
        ]
    )
    async_add_app_entities(
        entry,
        async_add_entities,
        lambda key: [
            AppStatusSensor(runtime.docker, entry, key),
            AppCpuSensor(runtime.stats, entry, key),
            AppMemorySensor(runtime.stats, entry, key),
        ],
    )


class AppStatusSensor(AppEntity, SensorEntity):
    _attr_device_class = SensorDeviceClass.ENUM
    _attr_options = APP_STATES
    _unrecorded_attributes = frozenset({"containers"})

    def __init__(self, coordinator, entry, app_key):
        super().__init__(coordinator, entry, app_key, "status")

    async def async_added_to_hass(self) -> None:
        await super().async_added_to_hass()
        self.async_on_remove(
            self.runtime.manager.add_listener(self.async_write_ha_state)
        )

    @property
    def native_value(self) -> str:
        return app_status(
            self.docker_data.app_containers(self.app_key),
            self.runtime.manager.busy.get(self.app_key),
        )

    @property
    def entity_picture(self) -> str | None:
        return (self.runtime.manager.get(self.app_key).store or {}).get("icon")

    @property
    def extra_state_attributes(self) -> dict:
        manager = self.runtime.manager
        web = app_web(self.runtime, self.app) if self.app else None
        return {
            # read by the Docker Apps dashboard cards
            "docker_app": self.app_key,
            "launcher": manager.in_launcher(self.app_key),
            "web_port": web[0] if web else None,
            "web_path": web[1] if web else None,
            "web_scheme": web[2] if web else None,
            "managed": manager.is_managed(self.app_key),
            "busy": manager.busy.get(self.app_key),
            "containers": [
                {
                    "name": c.name,
                    "service": c.service,
                    "state": c.state,
                    "health": c.health,
                    "image": c.image,
                    "started": c.started_at.isoformat() if c.started_at else None,
                    "restarts": c.restart_count,
                    "ports": c.ports,
                }
                for c in self.docker_data.app_containers(self.app_key)
            ],
        }


class _AppStatsSensor(AppEntity, SensorEntity):
    _attr_state_class = SensorStateClass.MEASUREMENT
    _unrecorded_attributes = frozenset({"containers"})

    def _values(self) -> dict[str, float]:
        raise NotImplementedError

    @property
    def available(self) -> bool:
        return super().available and self.coordinator.last_update_success

    @property
    def native_value(self) -> float | None:
        values = self._values()
        if not values:
            return None
        return round(sum(values.values()), 2)

    @property
    def extra_state_attributes(self) -> dict:
        return {"containers": self._values()}

    def _stats(self):
        stats = self.coordinator.data or {}
        keys = self.app.container_keys if self.app else []
        return {key: stats[key] for key in keys if key in stats}


class AppCpuSensor(_AppStatsSensor):
    _attr_native_unit_of_measurement = PERCENTAGE
    _attr_suggested_display_precision = 1

    def __init__(self, coordinator, entry, app_key):
        super().__init__(coordinator, entry, app_key, "cpu")

    def _values(self) -> dict[str, float]:
        return {
            k: s.cpu_percent
            for k, s in self._stats().items()
            if s.cpu_percent is not None
        }


class AppMemorySensor(_AppStatsSensor):
    _attr_device_class = SensorDeviceClass.DATA_SIZE
    _attr_native_unit_of_measurement = UnitOfInformation.MEBIBYTES
    _attr_suggested_display_precision = 0

    def __init__(self, coordinator, entry, app_key):
        super().__init__(coordinator, entry, app_key, "memory")

    def _values(self) -> dict[str, float]:
        return {
            k: round(s.memory_usage / 1048576, 1)
            for k, s in self._stats().items()
            if s.memory_usage is not None
        }


class HostContainersSensor(HostEntity, SensorEntity):
    _attr_state_class = SensorStateClass.MEASUREMENT

    def __init__(self, coordinator, entry):
        super().__init__(coordinator, entry, "containers_running")

    @property
    def native_value(self) -> int:
        return self.coordinator.data.host.containers_running

    @property
    def extra_state_attributes(self) -> dict:
        host = self.coordinator.data.host
        return {
            "total": host.containers,
            "stopped": host.containers_stopped,
            "paused": host.containers_paused,
            "apps": len(self.coordinator.data.apps),
        }


class HostImagesSensor(HostEntity, SensorEntity):
    _attr_entity_category = EntityCategory.DIAGNOSTIC

    def __init__(self, coordinator, entry):
        super().__init__(coordinator, entry, "images")

    @property
    def native_value(self) -> int:
        return self.coordinator.data.host.images


class HostReclaimableSensor(HostEntity, SensorEntity):
    _attr_device_class = SensorDeviceClass.DATA_SIZE
    _attr_native_unit_of_measurement = UnitOfInformation.BYTES
    _attr_suggested_unit_of_measurement = UnitOfInformation.GIGABYTES
    _attr_suggested_display_precision = 2
    _attr_entity_category = EntityCategory.DIAGNOSTIC

    def __init__(self, coordinator, entry):
        super().__init__(coordinator, entry, "reclaimable")

    @property
    def native_value(self) -> int | None:
        disk = self.coordinator.data
        return disk.images_reclaimable + disk.volumes_reclaimable if disk else None

    @property
    def extra_state_attributes(self) -> dict:
        disk = self.coordinator.data
        if not disk:
            return {}
        return {
            "images_size": disk.images_size,
            "images_unused": disk.images_unused,
            "volumes_size": disk.volumes_size,
            "volumes_unused": disk.volumes_unused,
            "build_cache_size": disk.build_cache_size,
        }
