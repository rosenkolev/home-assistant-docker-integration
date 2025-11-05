"""Load Platform integration."""

from homeassistant.helpers.typing import ConfigType
from homeassistant.core import HomeAssistant
#from homeassistant.helpers import device_registry as dr
from homeassistant.helpers.entity_component import EntityComponent

from .const import DOMAIN, _LOGGER
from .coordinator import DockerDataUpdateCoordinator
from .entity import DockerHost, DockerHostConfigEntry

PLATFORMS = []
COORDINATOR = "COORDINATOR"
DOCKER_HOST = "DOCKER_HOST"

async def async_setup(hass: HomeAssistant, config: ConfigType):
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

async def async_setup_entry(hass: HomeAssistant, entry: DockerHostConfigEntry) -> bool:
    _LOGGER.debug(f"__init__ async_setup_entry {entry.entry_id}")

    #async_register_docker_hub(hass, entry.entry_id)
    hass_domain_data = hass.data.setdefault(DOMAIN, {})

    coordinator = hass_domain_data[COORDINATOR] = DockerDataUpdateCoordinator(hass, entry)
    await coordinator.initialize()

    host = hass_domain_data[DOCKER_HOST] = DockerHost(hass)
    component = EntityComponent[DockerHost](_LOGGER, DOMAIN, hass)
    await component.async_add_entities([host])
    entry.runtime_data = host
    entry.async_on_unload(host.remove_listeners)

    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)
    return True

async def async_unload_entry(hass: HomeAssistant, entry: DockerHostConfigEntry) -> bool:
    _LOGGER.debug(f"__init__ async_unload_entry {entry.entry_id}")

    if unload_ok := await hass.config_entries.async_unload_platforms(entry, PLATFORMS):
        await hass.data[DOMAIN][COORDINATOR].disconnect()
        await hass.data[DOMAIN][DOCKER_HOST].async_remove()
        hass.data[DOMAIN] = {}

    return unload_ok

# async def async_register_docker_hub(hass: HomeAssistant, hub_id: str):
#     hub = DockerHub(hub_id)
#     hass.data.setdefault(DOMAIN, hub)

#     # connect
#     _LOGGER.info("autoconnect")
#     hub.auto_connect()

#     # register
#     device_registry = dr.async_get(hass)
#     device_registry.async_get_or_create(
#         config_entry_id=hub_id,
#         identifiers={(DOMAIN, hub.entity_id)},
#         manufacturer="Local",
#         name="Docker",
#         model="v1",
#         sw_version=hub.firmware_version,
#         hw_version=hub.hardware_version,
#     )
