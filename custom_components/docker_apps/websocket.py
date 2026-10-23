"""Websocket API for the Docker Apps panel (read-only views and live logs)."""

from __future__ import annotations

import asyncio
from dataclasses import asdict

import voluptuous as vol
from homeassistant.components import websocket_api
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers import device_registry as dr
from homeassistant.util import dt as dt_util

from ._compose_file import parse_services
from ._models import LABEL_PROJECT, split_image
from .catalog import CatalogError
from .const import DOMAIN
from .entity import app_identifier, app_web
from .services import get_container, get_runtime


@callback
def async_register_websocket(hass: HomeAssistant) -> None:
    for command in (ws_state, ws_store, ws_store_app, ws_resources, ws_logs_subscribe):
        websocket_api.async_register_command(hass, command)


@websocket_api.websocket_command({vol.Required("type"): f"{DOMAIN}/state"})
@websocket_api.require_admin
@callback
def ws_state(
    hass: HomeAssistant, connection: websocket_api.ActiveConnection, msg: dict
) -> None:
    entry, runtime = get_runtime(hass)
    data = runtime.docker.data
    stats = runtime.stats.data or {}
    updates = runtime.updates.data or {}
    manager = runtime.manager
    devices = dr.async_get(hass)

    apps = []
    for app in sorted(data.apps.values(), key=lambda a: a.key):
        settings = manager.get(app.key)
        update = updates.get(app.key)
        device = devices.async_get_device_by_identifier(
            app_identifier(entry.entry_id, app.key), entry.entry_id
        )
        tracking = manager.service_images(app.key, data)
        service_updates = {s.service: s for s in update.services} if update else {}
        containers = []
        for c in data.app_containers(app.key):
            s = stats.get(c.key)
            su = service_updates.get(c.service)
            containers.append(
                {
                    "key": c.key,
                    "name": c.name,
                    "service": c.service,
                    "image": c.image,
                    "state": c.state,
                    "health": c.health,
                    "started": c.started_at.isoformat() if c.started_at else None,
                    "restarts": c.restart_count,
                    "cpu": s.cpu_percent if s else None,
                    "memory": s.memory_usage if s else None,
                    "tracked": tracking[c.service or c.name][1],
                    "latest": su.latest if su and su.has_update else None,
                    "required": bool(su and su.required),
                    "is_self": c.id == manager.self_container_id,
                }
            )
        apps.append(
            {
                "key": app.key,
                "name": (settings.store or {}).get("name") or app.name,
                "icon": (settings.store or {}).get("icon"),
                "compose": app.is_compose,
                "editable": manager.can_edit(app),
                "managed": settings.managed,
                "launcher": manager.in_launcher(app.key),
                "version": (settings.store or {}).get("version")
                or next((c.image_version for c in data.app_containers(app.key)), None),
                "store": bool(settings.store),
                "include": settings.include,
                "exclude": settings.exclude,
                "upstream": settings.upstream,
                "allow_db_minor": settings.allow_db_minor,
                "busy": manager.busy.get(app.key),
                "progress": manager.progress.get(app.key),
                "web": app_web(runtime, app),
                "device_id": device.id if device else None,
                "update": (
                    {
                        "installed": update.installed_version,
                        "latest": update.latest_version,
                        "available": update.has_update,
                        "error": update.error,
                        "blocked": update.blocked,
                        "store": bool(update.store_app),
                        "release_notes": (
                            update.store_app.release_notes if update.store_app else None
                        ),
                        "changes": [
                            {
                                "service": s.service,
                                "image": split_image(s.image)[0],
                                "current": s.current,
                                "latest": s.latest,
                                "required": s.required,
                            }
                            for s in update.targets
                        ],
                        "kept": [
                            {
                                "service": s.service,
                                "image": s.image,
                                "latest": s.latest if s.has_update else None,
                            }
                            for s in update.services
                            if not s.tracked and not s.required
                        ],
                    }
                    if update
                    else None
                ),
                "history": manager.history_versions(app.key),
                "containers": containers,
            }
        )

    host = data.host
    connection.send_result(
        msg["id"],
        {
            "host": {
                "name": host.name,
                "version": host.version,
                "arch": host.arch,
                "containers": host.containers,
                "running": host.containers_running,
            },
            "disk": asdict(runtime.disk.data) if runtime.disk.data else None,
            # the browser clock can differ from the Docker host; uptime uses this
            "now": dt_util.utcnow().isoformat(),
            "read_only": runtime.read_only,
            "mounted": manager.paths.as_list(),
            "apps_root": manager.apps_root,
            "data_root": manager.data_root,
            "apps": apps,
        },
    )


@websocket_api.websocket_command({vol.Required("type"): f"{DOMAIN}/store"})
@websocket_api.require_admin
@callback
def ws_store(
    hass: HomeAssistant, connection: websocket_api.ActiveConnection, msg: dict
) -> None:
    _, runtime = get_runtime(hass)
    catalog = runtime.catalog
    arch = runtime.docker.data.host.arch
    installed = {
        f"{s.store['repo_id']}.{s.store['app_id']}": key
        for key, s in runtime.manager.settings.items()
        if s.store
    }
    connection.send_result(
        msg["id"],
        {
            "repositories": [
                {"id": r.id, "name": r.name, "error": catalog.errors.get(r.id)}
                for r in catalog.repositories.values()
            ],
            "apps": [
                {
                    "id": a.id,
                    "repo_id": a.repo_id,
                    "name": a.name,
                    "tagline": a.tagline,
                    "icon": a.icon,
                    "categories": a.categories,
                    "version": a.version,
                    "supported": a.supports(arch),
                    "installed": installed.get(a.id),
                }
                for a in sorted(catalog.apps.values(), key=lambda a: a.name.lower())
            ],
        },
    )


@websocket_api.websocket_command(
    {vol.Required("type"): f"{DOMAIN}/store/app", vol.Required("app_id"): str}
)
@websocket_api.require_admin
@websocket_api.async_response
async def ws_store_app(
    hass: HomeAssistant, connection: websocket_api.ActiveConnection, msg: dict
) -> None:
    _, runtime = get_runtime(hass)
    try:
        app = runtime.catalog.get(msg["app_id"])
    except CatalogError as err:
        connection.send_error(msg["id"], websocket_api.ERR_NOT_FOUND, str(err))
        return
    detail = app.as_dict(with_compose=False)
    detail["services"] = [
        {"name": name, "image": spec.get("image")}
        for name, spec in parse_services(app.compose or "").items()
    ]
    connection.send_result(msg["id"], detail)


@websocket_api.websocket_command({vol.Required("type"): f"{DOMAIN}/resources"})
@websocket_api.require_admin
@websocket_api.async_response
async def ws_resources(
    hass: HomeAssistant, connection: websocket_api.ActiveConnection, msg: dict
) -> None:
    _, runtime = get_runtime(hass)
    client = runtime.client
    images, volumes, networks = await asyncio.gather(
        client.async_images(), client.async_volumes(), client.async_networks()
    )
    used_images = {c.image_id for c in runtime.docker.data.containers.values()}

    def resource(item: dict) -> dict:
        return {
            "name": item["Name"],
            "driver": item.get("Driver"),
            "project": (item.get("Labels") or {}).get(LABEL_PROJECT),
        }

    connection.send_result(
        msg["id"],
        {
            "images": [
                {
                    "id": i["Id"],
                    "tags": i.get("RepoTags") or [],
                    "size": i.get("Size", 0),
                    "created": i.get("Created"),
                    "in_use": i["Id"] in used_images,
                }
                for i in images
            ],
            "volumes": [resource(v) for v in volumes],
            "networks": [resource(n) for n in networks],
        },
    )


@websocket_api.websocket_command(
    {
        vol.Required("type"): f"{DOMAIN}/logs/subscribe",
        vol.Required("container"): str,
        vol.Optional("tail", default=200): vol.All(int, vol.Range(0, 5000)),
    }
)
@websocket_api.require_admin
@callback
def ws_logs_subscribe(
    hass: HomeAssistant, connection: websocket_api.ActiveConnection, msg: dict
) -> None:
    """Stream log lines of one container until the panel unsubscribes."""
    _, runtime = get_runtime(hass)
    container = get_container(runtime, msg["container"])
    batch: list[str] = []

    def send(payload: dict) -> None:
        connection.send_message(websocket_api.event_message(msg["id"], payload))

    def flush() -> None:
        if batch:
            send({"lines": list(batch)})
            batch.clear()

    async def flush_every_300ms() -> None:
        while True:
            await asyncio.sleep(0.3)
            flush()

    async def stream() -> None:
        timer = asyncio.create_task(flush_every_300ms())
        try:
            async for line in runtime.client.async_container_logs_follow(
                container.id, tail=msg["tail"], tty=container.tty
            ):
                batch.append(line)
            flush()
            send({"end": True})
        except Exception as err:  # noqa: BLE001
            send({"error": str(err)})
        finally:
            timer.cancel()

    connection.send_result(msg["id"])
    task = hass.async_create_background_task(stream(), f"{DOMAIN} logs {container.key}")
    connection.subscriptions[msg["id"]] = task.cancel
