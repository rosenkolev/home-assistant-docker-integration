"""Load Platform integration."""

from homeassistant.config_entries import SOURCE_IMPORT
from homeassistant.const import Platform
from homeassistant.core import HomeAssistant
from homeassistant.helpers import config_validation as cv
from homeassistant.helpers import device_registry as dr
from homeassistant.helpers.device_registry import DeviceEntryType
from homeassistant.helpers.typing import ConfigType

from .const import _LOGGER, DATA_KEY_RESOURCE_REGISTRY, DOMAIN, FRONTEND_URL
from .coordinator import DockerConfigEntry, ServiceController
from .frontend import (
    FrontendResourcesRegistry,
    async_register_static_path_to_hass_router,
)
from .services import async_register_services, async_remove_services

PLATFORMS = [Platform.SENSOR, Platform.SWITCH, Platform.BUTTON, Platform.BINARY_SENSOR]
CONFIG_SCHEMA = cv.empty_config_schema(DOMAIN)


async def async_setup(hass: HomeAssistant, config: ConfigType):
    # register an url to home assistant router
    await async_register_static_path_to_hass_router(
        hass, FRONTEND_URL, path="www", cache_headers=False
    )
    hass.data.setdefault(DOMAIN, {})[DATA_KEY_RESOURCE_REGISTRY] = (
        FrontendResourcesRegistry(hass, version="0.50")
    )

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


async def async_setup_entry(hass: HomeAssistant, entry: DockerConfigEntry) -> bool:
    _LOGGER.debug(f"__init__ async_setup_entry {entry.entry_id}")

    # init controller and fetch initial data
    controller = ServiceController(hass, entry)

    # set controller to entity for easy access
    entry.runtime_data = controller

    # connect the API and start the coordinators
    await controller.async_initialize()

    # register (update) service information
    device_registry = dr.async_get(hass)
    device_registry.async_get_or_create(
        config_entry_id=entry.entry_id,
        identifiers={(DOMAIN, entry.entry_id)},
        entry_type=DeviceEntryType.SERVICE,
        name=controller.name,
        sw_version=controller.version,
    )

    # register entities
    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)

    # register services
    async_register_services(hass)

    # register frontend panel
    resources: FrontendResourcesRegistry = hass.data[DOMAIN][DATA_KEY_RESOURCE_REGISTRY]
    await resources.async_register_resource(f"{FRONTEND_URL}/docker_dashboard.js")
    resources.register_yaml_panel(
        url="dashboard-docker",
        config_file="docker_dashboard.yaml",
        title="Docker",
        icon="mdi:docker",
        require_admin=True,
    )

    return True


async def async_unload_entry(hass: HomeAssistant, entry: DockerConfigEntry) -> bool:
    _LOGGER.debug(f"__init__ async_unload_entry {entry.entry_id}")

    if unload_ok := await hass.config_entries.async_unload_platforms(entry, PLATFORMS):
        # stop coordinators and disconnect docker client
        await entry.runtime_data.async_shutdown()
        entry.runtime_data = None

        # unload services
        async_remove_services(hass)

        # unload frontend resources
        registry: FrontendResourcesRegistry = hass.data[DOMAIN][
            DATA_KEY_RESOURCE_REGISTRY
        ]
        await registry.async_unload_frontend_resources()

    return unload_ok
