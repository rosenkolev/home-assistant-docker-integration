import sys
from unittest.mock import Mock


class MockedBaseEntity:
    ha_state_update_scheduled = False
    ha_state_update_scheduled_force_refresh = False
    ha_state_write = False
    ha_added_to_hass = False
    hass = 1

    @property
    def name(self) -> str:
        return self._attr_name

    @property
    def unique_id(self) -> str:
        return self._attr_unique_id

    def async_write_ha_state(self):
        self.ha_state_write = True

    def async_schedule_update_ha_state(self, force_refresh=False):
        self.ha_state_update_scheduled = True
        self.ha_state_update_scheduled_force_refresh = force_refresh

    def schedule_update_ha_state(self, force_refresh=False):
        self.ha_state_update_scheduled = True
        self.ha_state_update_scheduled_force_refresh = False

    async def async_added_to_hass(self) -> None:
        self.ha_added_to_hass = True

    def async_on_remove(self, _=None):
        pass

    async def async_will_remove_from_hass(self) -> None:
        pass


class MockedCoordinator[TData]:
    def __init__(self, hass, logger, config_entry, name, update_interval):
        self.config_entry = config_entry
        self.data: TData = None


class MockedCoordinatorEntity[TCoordinator]:
    def __init__(self, coordinator: TCoordinator):
        self.coordinator = coordinator


class Platform:
    SWITCH = "switch"
    LIGHT = "light"
    COVER = "cover"
    BINARY_SENSOR = "binary_sensor"
    FAN = "fan"
    SENSOR = "sensor"
    NUMBER = "number"
    BUTTON = "button"


class DeviceInfo:
    def __init__(
        self,
        identifiers,
        name,
        model,
        model_id=None,
        manufacturer=None,
        sw_version=None,
        via_device=None,
    ):
        self.identifiers = identifiers
        self.name = name
        self.manufacturer = manufacturer
        self.model = model
        self.model_id = model_id
        self.sw_version = sw_version
        self.via_device = via_device


class MockVolSchema:
    def __init__(self, schema, extra=None):
        self.schema = schema


class MockColOptional:
    def __init__(self, name, default=None, description=None):
        self.schema = name
        self.default = lambda: default


sys.modules["voluptuous"] = Mock()
sys.modules["voluptuous"].Schema = MockVolSchema
sys.modules["voluptuous"].Optional = MockColOptional
sys.modules["voluptuous"].ALLOW_EXTRA = "ALLOW_EXTRA"
sys.modules["homeassistant"] = Mock()
sys.modules["homeassistant.const"] = Mock()
sys.modules["homeassistant.const"].Platform = Platform
sys.modules["homeassistant.const"].CONF_NAME = "CONF_NAME"
sys.modules["homeassistant.const"].CONF_PORT = "CONF_PORT"
sys.modules["homeassistant.const"].CONF_UNIQUE_ID = "CONF_UNIQUE_ID"
sys.modules["homeassistant.core"] = Mock()
sys.modules["homeassistant.config_entries"] = Mock()
sys.modules["homeassistant.config_entries"].SOURCE_IMPORT = "SOURCE_IMPORT"
sys.modules["homeassistant.helpers"] = Mock()
sys.modules["homeassistant.helpers.config_validation"] = Mock()
sys.modules["homeassistant.helpers.device_registry"] = Mock()
sys.modules["homeassistant.helpers.device_registry"].DeviceInfo = DeviceInfo
sys.modules["homeassistant.helpers.entity_platform"] = Mock()
sys.modules["homeassistant.helpers.update_coordinator"] = Mock()
sys.modules["homeassistant.helpers.update_coordinator"].DataUpdateCoordinator = (
    MockedCoordinator
)
sys.modules["homeassistant.helpers.update_coordinator"].CoordinatorEntity = (
    MockedCoordinatorEntity
)
sys.modules["homeassistant.helpers.event"] = Mock()
sys.modules["homeassistant.helpers.selector"] = Mock()
sys.modules["homeassistant.helpers.typing"] = Mock()
sys.modules["homeassistant.exceptions"] = Mock()
sys.modules["homeassistant.components"] = Mock()


class CoverEntityFeature:
    OPEN = 1
    CLOSE = 2
    STOP = 4
    SET_POSITION = 8


sys.modules["homeassistant.components.cover"] = Mock()
sys.modules["homeassistant.components.cover"].CoverEntity = MockedBaseEntity
sys.modules["homeassistant.components.cover"].CoverEntityFeature = CoverEntityFeature
sys.modules["homeassistant.components.cover"].ATTR_POSITION = "A_POSITION"

sys.modules["homeassistant.components.binary_sensor"] = Mock()
sys.modules["homeassistant.components.binary_sensor"].BinarySensorEntity = (
    MockedBaseEntity
)
sys.modules["homeassistant.components.sensor"] = Mock()
sys.modules["homeassistant.components.sensor"].SensorEntity = MockedBaseEntity
sys.modules["homeassistant.components.switch"] = Mock()
sys.modules["homeassistant.components.switch"].SwitchEntity = MockedBaseEntity


class LightEntityFeature:
    FLASH = 1
    EFFECT = 2


sys.modules["homeassistant.components.light"] = Mock()
sys.modules["homeassistant.components.light"].LightEntity = MockedBaseEntity
sys.modules["homeassistant.components.light"].LightEntityFeature = LightEntityFeature()
sys.modules["homeassistant.components.light"].ATTR_BRIGHTNESS = "A_BRIGHTNESS"
sys.modules["homeassistant.components.light"].ATTR_EFFECT = "A_EFFECT"
sys.modules["homeassistant.components.light"].ATTR_FLASH = "A_FLASH"
sys.modules["homeassistant.components.light"].ATTR_RGB_COLOR = "A_RGB"
sys.modules["homeassistant.components.light"].EFFECT_OFF = "E_OFF"
sys.modules["homeassistant.components.light"].FLASH_SHORT = "F_SHORT"
sys.modules["homeassistant.components.light"].FLASH_LONG = "F_LONG"


class FanEntityFeature:
    SET_SPEED = 1
    TURN_ON = 2
    TURN_OFF = 4


sys.modules["homeassistant.components.fan"] = Mock()
sys.modules["homeassistant.components.fan"].FanEntityFeature = FanEntityFeature()
sys.modules["homeassistant.components.fan"].FanEntity = MockedBaseEntity
sys.modules["homeassistant.components.fan"].ATTR_PERCENTAGE = "A_PERCENTAGE"

sys.modules["homeassistant.components.number"] = Mock()
sys.modules["homeassistant.components.number"].NumberEntity = MockedBaseEntity


class ButtonDeviceClass:
    RESTART = 1


sys.modules["homeassistant.components.button"] = Mock()
sys.modules["homeassistant.components.button"].ButtonEntity = MockedBaseEntity
sys.modules["homeassistant.components.button"].ButtonDeviceClass = ButtonDeviceClass

sys.modules["homeassistant.components.frontend"] = Mock()
sys.modules["homeassistant.components.frontend"].DATA_PANELS = "frontend_panels"

sys.modules["homeassistant.components.http"] = Mock()
sys.modules["homeassistant.components.lovelace.const"] = Mock()
sys.modules["homeassistant.components.lovelace.dashboard"] = Mock()
sys.modules["homeassistant.components.lovelace.resources"] = Mock()

sys.modules["docker"] = Mock()
sys.modules["docker.errors"] = Mock()
sys.modules["docker.errors"].APIError = Exception
sys.modules["docker.errors"].ImageNotFound = Exception
sys.modules["docker.errors"].NotFound = Exception
