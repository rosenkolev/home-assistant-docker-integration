import voluptuous as vol
from homeassistant.core import (
    HomeAssistant,
    ServiceCall,
    ServiceResponse,
    SupportsResponse,
    callback,
)
from homeassistant.helpers import config_validation as cv

from .const import _LOGGER, DOMAIN
from .coordinator import DockerConfigEntry

CONF_IMAGE = "image"
CONF_NAME = "name"
CONF_NETWORK = "network"
CONF_PORTS = "ports"
CONF_VOLUMES = "volumes"
CONF_RESTART_POLICY = "restart_policy"

CREATE_CONTAINER_SERVICE = "create"
CREATE_CONTAINER_SERVICE_SCHEMA = vol.Schema(
    {
        vol.Required(CONF_IMAGE): cv.string,
        vol.Required(CONF_NAME): cv.string,
        vol.Optional(CONF_NETWORK): cv.string,
        vol.Optional(CONF_PORTS): vol.All(cv.ensure_list, [cv.string]),
        vol.Optional(CONF_VOLUMES): vol.All(cv.ensure_list, [cv.string]),
        vol.Optional(CONF_RESTART_POLICY): cv.string,
    }
)


async def _async_handle_create(call: ServiceCall) -> ServiceResponse:
    """Create new container."""
    # gets the config entry
    entires: list[DockerConfigEntry] = call.hass.config_entries.async_loaded_entries(
        DOMAIN
    )
    if not entires:
        _LOGGER.error("Service can't be called because no active config_entries")
        return

    restart_policy = call.data.get(CONF_RESTART_POLICY)
    if restart_policy:
        restart_policy = {"Name": restart_policy}

    entires[0].runtime_data.api.async_container_create(
        image=call.data.get(CONF_IMAGE),
        name=call.data.get(CONF_NAME),
        network=call.data.get(CONF_NETWORK),
        ports=call.data.get(CONF_PORTS),
        volumes=call.data.get(CONF_VOLUMES),
        restart_policy=restart_policy,
    )


@callback
def async_register_services(hass: HomeAssistant) -> None:
    """Register Google Photos services."""

    hass.services.async_register(
        DOMAIN,
        CREATE_CONTAINER_SERVICE,
        _async_handle_create,
        schema=CREATE_CONTAINER_SERVICE_SCHEMA,
        supports_response=SupportsResponse.OPTIONAL,
    )


@callback
def async_remove_services(hass: HomeAssistant) -> None:
    hass.services.async_remove(DOMAIN, CREATE_CONTAINER_SERVICE)
