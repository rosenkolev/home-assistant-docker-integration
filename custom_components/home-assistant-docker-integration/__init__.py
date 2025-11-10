"""Load Platform integration."""

from homeassistant.config_entries import SOURCE_IMPORT, ConfigEntry
from homeassistant.const import Platform
from homeassistant.core import HomeAssistant
from homeassistant.helpers import config_validation as cv
from homeassistant.helpers import device_registry as dr
from homeassistant.helpers.device_registry import DeviceEntryType
from homeassistant.helpers.typing import ConfigType

from .const import _LOGGER, COORDINATOR, DOMAIN
from .coordinator import DockerDataUpdateCoordinator
from .frontend import async_register_frontend

PLATFORMS = [Platform.SENSOR, Platform.SWITCH, Platform.BUTTON, Platform.BINARY_SENSOR]
CONFIG_SCHEMA = cv.empty_config_schema(DOMAIN)


async def async_setup(hass: HomeAssistant, config: ConfigType):
    await async_register_frontend(hass)

    if not hass.config_entries.async_entries(DOMAIN):
        # We avoid creating an import flow if its already
        # setup since it will have to import the config_flow
        # module.
        hass.async_create_task(
            hass.config_entries.flow.async_init(
                DOMAIN,
                context={"source": SOURCE_IMPORT},
                data=config,
            )
        )

    return True


async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    _LOGGER.debug(f"__init__ async_setup_entry {entry.entry_id}")

    # init coordinator and fetch initial data
    domain_data = hass.data.setdefault(DOMAIN, {})
    coordinator = domain_data[COORDINATOR] = DockerDataUpdateCoordinator(hass, entry)
    await coordinator.async_config_entry_first_refresh()

    # set coordinator to entity for easy access
    entry.runtime_data = coordinator

    # register (update) service information
    device_registry = dr.async_get(hass)
    device_registry.async_get_or_create(
        config_entry_id=entry.entry_id,
        identifiers={(DOMAIN, entry.entry_id)},
        entry_type=DeviceEntryType.SERVICE,
        name="Docker Host",
        sw_version=coordinator.data.version,
    )

    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)
    return True


async def async_unload_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    _LOGGER.debug(f"__init__ async_unload_entry {entry.entry_id}")

    if unload_ok := await hass.config_entries.async_unload_platforms(entry, PLATFORMS):
        await hass.data[DOMAIN][COORDINATOR].async_disconnect()
        hass.data[DOMAIN] = {}

    return unload_ok
