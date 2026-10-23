"""Actions: logs on demand, manage, recreate, container control, prune, store."""

# cspell:words coro

from __future__ import annotations

import voluptuous as vol
from homeassistant.core import (
    HomeAssistant,
    ServiceCall,
    ServiceResponse,
    SupportsResponse,
    callback,
)
from homeassistant.exceptions import HomeAssistantError, ServiceValidationError
from homeassistant.helpers import config_validation as cv
from homeassistant.helpers.service import async_register_admin_service

from ._docker_client import DockerError
from ._models import AppInfo, ContainerInfo
from .apps import AppError
from .catalog import CatalogError
from .const import DOMAIN
from .coordinator import DockerAppsConfigEntry, DockerAppsRuntime
from .entity import async_remove_stale_devices

ATTR_APP = "app"
ATTR_CONTAINER = "container"
MAX_TAIL = 5000

# prune kind -> (Docker resource, filters)
PRUNE = {
    "containers": ("containers", None),
    "images": ("images", {"dangling": ["true"]}),
    "images_unused": ("images", {"dangling": ["false"]}),
    # Docker 23+ only prunes anonymous volumes unless all=true
    "volumes": ("volumes", {"all": ["true"]}),
    "networks": ("networks", None),
    "build_cache": ("build", None),
}


def get_runtime(hass: HomeAssistant) -> tuple[DockerAppsConfigEntry, DockerAppsRuntime]:
    entries = hass.config_entries.async_loaded_entries(DOMAIN)
    if not entries:
        raise ServiceValidationError("Docker Apps is not loaded")
    return entries[0], entries[0].runtime_data


def get_app(runtime: DockerAppsRuntime, app_key: str) -> AppInfo:
    if app := runtime.docker.data.apps.get(app_key):
        return app
    raise ServiceValidationError(f"app '{app_key}' not found")


def get_container(runtime: DockerAppsRuntime, key: str) -> ContainerInfo:
    containers = runtime.docker.data.containers
    if key in containers:
        return containers[key]
    for container in containers.values():
        if key in (container.name, container.short_id, container.id):
            return container
    raise ServiceValidationError(f"container '{key}' not found")


async def _call(coro):
    try:
        return await coro
    except (AppError, DockerError, CatalogError) as err:
        raise HomeAssistantError(str(err)) from err


@callback
def async_register_services(hass: HomeAssistant) -> None:
    def writable_runtime() -> DockerAppsRuntime:
        _, runtime = get_runtime(hass)
        if runtime.read_only:
            raise ServiceValidationError("Docker Apps is in read-only mode")
        return runtime

    async def logs(call: ServiceCall) -> ServiceResponse:
        _, runtime = get_runtime(hass)
        if key := call.data.get(ATTR_CONTAINER):
            containers = [get_container(runtime, key)]
        elif app_key := call.data.get(ATTR_APP):
            containers = runtime.docker.data.app_containers(
                get_app(runtime, app_key).key
            )
        else:
            raise ServiceValidationError("give an app or a container")
        return {
            "logs": {
                c.key: await _call(
                    runtime.client.async_container_logs(
                        c.id,
                        tail=call.data["tail"],
                        since=call.data.get("since"),
                        timestamps=True,
                        tty=c.tty,
                    )
                )
                for c in containers
            }
        }

    async def manage(call: ServiceCall) -> None:
        _, runtime = get_runtime(hass)
        changes = dict(call.data)
        app = get_app(runtime, changes.pop(ATTR_APP))
        await runtime.manager.async_configure(app.key, **changes)
        await runtime.updates.async_check_now()

    async def update_services(call: ServiceCall) -> ServiceResponse:
        runtime = writable_runtime()
        app = get_app(runtime, call.data[ATTR_APP])
        changes = await _call(
            runtime.manager.async_update_services(
                app, call.data["services"], call.data.get("tag"), runtime.docker.data
            )
        )
        await runtime.docker.async_refresh()
        await runtime.updates.async_check_now()
        return {"updated": changes}

    async def recreate(call: ServiceCall) -> None:
        runtime = writable_runtime()
        app = get_app(runtime, call.data[ATTR_APP])
        await _call(
            runtime.manager.async_recreate(app, runtime.docker.data, call.data["pull"])
        )
        await runtime.docker.async_refresh()

    async def container_action(call: ServiceCall) -> None:
        runtime = writable_runtime()
        container = get_container(runtime, call.data[ATTR_CONTAINER])
        action = call.service.removeprefix("container_")
        if action == "stop" and container.id == runtime.manager.self_container_id:
            raise ServiceValidationError("Home Assistant can't stop itself")
        await _call(runtime.client.async_container_action(container.id, action))
        await runtime.docker.async_refresh()

    async def prune(call: ServiceCall) -> ServiceResponse:
        runtime = writable_runtime()
        resource, filters = PRUNE[call.data["kind"]]
        # ImagesDeleted lists every layer too, so images are counted before and after
        if resource == "images":
            before = len(await _call(runtime.client.async_images()))
        result = await _call(runtime.client.async_prune(resource, filters))
        await runtime.disk.async_refresh()
        await runtime.docker.async_refresh()
        if resource == "images":
            removed = before - len(await _call(runtime.client.async_images()))
        else:
            removed = sum(
                len(v) for k, v in result.items() if k.endswith("Deleted") and v
            )
        return {"space_reclaimed": result.get("SpaceReclaimed", 0), "removed": removed}

    async def check_updates(call: ServiceCall) -> None:
        _, runtime = get_runtime(hass)
        await runtime.updates.async_check_now(refresh_catalog=call.data["repositories"])

    async def install(call: ServiceCall) -> ServiceResponse:
        runtime = writable_runtime()
        store_app = runtime.catalog.get(call.data["store_app"])
        arch = runtime.docker.data.host.arch
        if not store_app.supports(arch):
            raise ServiceValidationError(f"{store_app.name} doesn't support {arch}")
        key = await _call(
            runtime.manager.async_install_store_app(
                store_app,
                app_key=call.data.get("name"),
                values=call.data.get("values"),
                data=runtime.docker.data,
            )
        )
        await runtime.docker.async_refresh()
        await runtime.updates.async_check_now()
        return {"app": key}

    async def uninstall(call: ServiceCall) -> None:
        runtime = writable_runtime()
        app = get_app(runtime, call.data[ATTR_APP])
        await _call(
            runtime.manager.async_uninstall(
                app, runtime.docker.data, call.data["remove_data"]
            )
        )
        await runtime.docker.async_refresh()
        async_remove_stale_devices(hass, get_runtime(hass)[0])

    hass.services.async_register(
        DOMAIN,
        "logs",
        logs,
        schema=vol.Schema(
            {
                vol.Optional(ATTR_APP): cv.string,
                vol.Optional(ATTR_CONTAINER): cv.string,
                vol.Optional("tail", default=100): vol.All(
                    vol.Coerce(int), vol.Range(1, MAX_TAIL)
                ),
                vol.Optional("since"): vol.Coerce(int),
            }
        ),
        supports_response=SupportsResponse.ONLY,
    )
    admin_services = {
        "manage": (
            manage,
            {
                vol.Required(ATTR_APP): cv.string,
                vol.Optional("managed"): cv.boolean,
                vol.Optional("launcher"): cv.boolean,
                vol.Optional("services"): vol.All(cv.ensure_list, [cv.string]),
                vol.Optional("include"): cv.string,
                vol.Optional("exclude"): cv.string,
                vol.Optional("upstream"): cv.string,
                vol.Optional("allow_db_minor"): cv.boolean,
            },
        ),
        "update_services": (
            update_services,
            {
                vol.Required(ATTR_APP): cv.string,
                vol.Required("services"): vol.All(cv.ensure_list, [cv.string]),
                vol.Optional("tag"): cv.string,
            },
        ),
        "recreate": (
            recreate,
            {
                vol.Required(ATTR_APP): cv.string,
                vol.Optional("pull", default=False): cv.boolean,
            },
        ),
        "container_start": (
            container_action,
            {vol.Required(ATTR_CONTAINER): cv.string},
        ),
        "container_stop": (container_action, {vol.Required(ATTR_CONTAINER): cv.string}),
        "container_restart": (
            container_action,
            {vol.Required(ATTR_CONTAINER): cv.string},
        ),
        "prune": (prune, {vol.Required("kind"): vol.In(list(PRUNE))}),
        "check_updates": (
            check_updates,
            {vol.Optional("repositories", default=False): cv.boolean},
        ),
        "install": (
            install,
            {
                vol.Required("store_app"): cv.string,
                vol.Optional("name"): cv.string,
                vol.Optional("values"): {cv.string: cv.string},
            },
        ),
        "uninstall": (
            uninstall,
            {
                vol.Required(ATTR_APP): cv.string,
                vol.Optional("remove_data", default=False): cv.boolean,
            },
        ),
    }
    with_response = {"update_services", "prune", "install"}
    for name, (handler, schema) in admin_services.items():
        async_register_admin_service(
            hass,
            DOMAIN,
            name,
            handler,
            vol.Schema(schema),
            supports_response=(
                SupportsResponse.OPTIONAL
                if name in with_response
                else SupportsResponse.NONE
            ),
        )
