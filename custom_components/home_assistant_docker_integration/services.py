import voluptuous as vol
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import (
    HomeAssistant,
    ServiceCall,
    ServiceResponse,
    SupportsResponse,
    callback,
)
from homeassistant.helpers import config_validation as cv

from .const import DOMAIN

CONF_IMAGE = "image"
CONF_NAME = "name"
CONF_NETWORK = "network"
CONF_PORTS = "ports"
CONF_VOLUMES = "volumes"

CREATE_SERVICE = "create"
CREATE_SERVICE_SCHEMA = vol.Schema(
    {
        vol.Required(CONF_IMAGE): cv.string,
        vol.Required(CONF_NAME): cv.string,
        vol.Optional(CONF_NETWORK): cv.string,
        vol.Optional(CONF_PORTS): vol.All(cv.ensure_list, [cv.string]),
        vol.Optional(CONF_VOLUMES): vol.All(cv.ensure_list, [cv.string]),
    }
)


async def _async_handle_create(call: ServiceCall) -> ServiceResponse:
    """Create new container."""
    pass


@callback
def async_setup_services(hass: HomeAssistant) -> None:
    """Register Google Photos services."""

    hass.services.async_register(
        DOMAIN,
        CREATE_SERVICE,
        _async_handle_create,
        schema=CREATE_SERVICE_SCHEMA,
        supports_response=SupportsResponse.OPTIONAL,
    )
