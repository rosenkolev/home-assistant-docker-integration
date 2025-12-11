import voluptuous as vol
from homeassistant.core import (
    HomeAssistant,
    ServiceCall,
    ServiceResponse,
    SupportsResponse,
    callback,
)
from homeassistant.helpers import config_validation as cv

from ._docker_api import DockerApi
from .const import _LOGGER, DOMAIN
from .coordinator import DockerConfigEntry

CONF_IMAGE = "image"
CONF_NAME = "name"
CONF_NETWORK = "network"
CONF_PORTS = "ports"
CONF_VOLUMES = "volumes"
CONF_RESTART_POLICY = "restart_policy"

CONF_ID = "id"

CREATE_SERVICE = "create"
START_SERVICE = "start"
STOP_SERVICE = "stop"
REMOVE_SERVICE = "remove"
LOGS_SERVICE = "logs"
RESTART_SERVICE = "restart"
PRUNE_VOLUMES_SERVICE = "prune_volumes"
PRUNE_CONTAINERS_SERVICE = "prune_containers"
PRUNE_IMAGES_SERVICE = "prune_images"
EMPTY_SERVICE_SCHEMA = vol.Schema({})
CONTAINER_SERVICE_SCHEMA = vol.Schema(
    {
        vol.Required(CONF_ID): cv.string,
    }
)

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


@callback
def _get_api(call: ServiceCall) -> DockerApi | None:
    entires: list[DockerConfigEntry] = call.hass.config_entries.async_loaded_entries(
        DOMAIN
    )
    if not entires:
        _LOGGER.error("Service can't be called because no active config_entries")
        return None
    return entires[0].runtime_data.api


async def _async_handle_create(call: ServiceCall) -> ServiceResponse:
    """Create new container."""
    api = _get_api(call)
    if not api:
        return

    restart_policy = call.data.get(CONF_RESTART_POLICY)
    if restart_policy:
        restart_policy = {"Name": restart_policy}

    api.async_container_create(
        image=call.data.get(CONF_IMAGE),
        name=call.data.get(CONF_NAME),
        network=call.data.get(CONF_NETWORK),
        ports=call.data.get(CONF_PORTS),
        volumes=call.data.get(CONF_VOLUMES),
        restart_policy=restart_policy,
    )


async def _async_handle_start(call: ServiceCall) -> ServiceResponse:
    api = _get_api(call)
    if api:
        await api.async_container_start(id=call.data.get(CONF_ID))


async def _async_handle_stop(call: ServiceCall) -> ServiceResponse:
    api = _get_api(call)
    if api:
        _LOGGER.debug(f"Stopping container {call.data}")
        await api.async_container_stop(id=call.data.get(CONF_ID))


async def _async_handle_remove(call: ServiceCall) -> ServiceResponse:
    """Remove container."""
    api = _get_api(call)
    if api:
        await api.async_container_remove(id=call.data.get(CONF_ID), remove_volumes=True)


async def _async_handle_restart(call: ServiceCall) -> ServiceResponse:
    api = _get_api(call)
    if api:
        await api.async_container_restart(id=call.data.get(CONF_ID))


async def _async_handle_logs(call: ServiceCall) -> ServiceResponse:
    """Get container logs."""
    api = _get_api(call)
    if api:
        logs = await api.async_container_logs(id=call.data.get(CONF_ID))
        _LOGGER.debug(f"Logs for container {call.data.get(CONF_ID)}: {logs}")
        return {"logs": logs}


async def _async_handle_prune_volumes(call: ServiceCall) -> ServiceResponse:
    api = _get_api(call)
    if api:
        await api.async_volumes_prune()


async def _async_handle_prune_containers(call: ServiceCall) -> ServiceResponse:
    api = _get_api(call)
    if api:
        await api.async_containers_prune()


async def _async_handle_prune_images(call: ServiceCall) -> ServiceResponse:
    api = _get_api(call)
    if api:
        await api.async_images_prune()


### Register service ###


@callback
def _register_call_service(
    hass: HomeAssistant,
    service: str,
    handler,
    supports_response=SupportsResponse.OPTIONAL,
) -> None:
    hass.services.async_register(
        DOMAIN,
        service,
        handler,
        schema=CONTAINER_SERVICE_SCHEMA,
        supports_response=supports_response,
    )


def _register_empty_service(hass: HomeAssistant, service: str, handler) -> None:
    hass.services.async_register(
        DOMAIN,
        service,
        handler,
        schema=EMPTY_SERVICE_SCHEMA,
        supports_response=SupportsResponse.OPTIONAL,
    )


@callback
def async_register_services(hass: HomeAssistant) -> None:
    """Register services."""

    hass.services.async_register(
        DOMAIN,
        CREATE_SERVICE,
        _async_handle_create,
        schema=CREATE_CONTAINER_SERVICE_SCHEMA,
        supports_response=SupportsResponse.OPTIONAL,
    )
    _register_call_service(hass, START_SERVICE, _async_handle_start)
    _register_call_service(hass, STOP_SERVICE, _async_handle_stop)
    _register_call_service(hass, REMOVE_SERVICE, _async_handle_remove)
    _register_call_service(
        hass, LOGS_SERVICE, _async_handle_logs, SupportsResponse.ONLY
    )
    _register_call_service(hass, RESTART_SERVICE, _async_handle_restart)
    _register_empty_service(hass, PRUNE_VOLUMES_SERVICE, _async_handle_prune_volumes)
    _register_empty_service(
        hass, PRUNE_CONTAINERS_SERVICE, _async_handle_prune_containers
    )
    _register_empty_service(hass, PRUNE_IMAGES_SERVICE, _async_handle_prune_images)


@callback
def async_remove_services(hass: HomeAssistant) -> None:
    hass.services.async_remove(DOMAIN, CREATE_SERVICE)
    hass.services.async_remove(DOMAIN, START_SERVICE)
    hass.services.async_remove(DOMAIN, STOP_SERVICE)
    hass.services.async_remove(DOMAIN, REMOVE_SERVICE)
    hass.services.async_remove(DOMAIN, LOGS_SERVICE)
    hass.services.async_remove(DOMAIN, RESTART_SERVICE)
    hass.services.async_remove(DOMAIN, PRUNE_VOLUMES_SERVICE)
    hass.services.async_remove(DOMAIN, PRUNE_CONTAINERS_SERVICE)
    hass.services.async_remove(DOMAIN, PRUNE_IMAGES_SERVICE)
