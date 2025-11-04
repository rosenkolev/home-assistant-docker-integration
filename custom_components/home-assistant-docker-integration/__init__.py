"""Load Platform integration."""

from homeassistant.config_entries import ConfigEntry
from homeassistant.helpers.typing import ConfigType
from homeassistant.core import HomeAssistant
from homeassistant.helpers import device_registry as dr

from .const import DOMAIN, _LOGGER
from .hub import DockerHub

async def async_setup(hass: HomeAssistant, config: ConfigType):
    _LOGGER.debug(f"__init__ async_setup {config}")
    # await async_register_docker_hub(hass)

    # def cleanup_hub(event):
    #     """Stuff to do before stopping."""
    #     hass.data[DOMAIN].close()

    # hass.bus.listen_once(EVENT_HOMEASSISTANT_STOP, cleanup_hub)
    return True

async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    _LOGGER.debug(f"__init__ async_setup_entry {entry.entry_id}")
    async_register_docker_hub(hass, entry.entry_id)
    return True

async def async_unload_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    _LOGGER.debug(f"__init__ async_unload_entry {entry.entry_id}")
    hub = hass.data[DOMAIN]
    if not hub is None:
        hub.close()

    return True

async def async_register_docker_hub(hass: HomeAssistant, hub_id: str):
    hub = DockerHub(hub_id)
    hass.data.setdefault(DOMAIN, hub)

    # connect
    _LOGGER.info("autoconnect")
    hub.auto_connect()

    # register
    device_registry = dr.async_get(hass)
    device_registry.async_get_or_create(
        config_entry_id=hub_id,
        identifiers={(DOMAIN, hub.entity_id)},
        manufacturer="Local",
        name="Docker",
        model="v1",
        #sw_version=hub.firmware_version,
        #hw_version=hub.hardware_version,
    )
