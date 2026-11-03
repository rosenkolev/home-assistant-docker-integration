import asyncio
from unittest.mock import MagicMock, patch

import pytest
from homeassistant.config_entries import ConfigEntryState
from homeassistant.exceptions import ServiceValidationError
from homeassistant.helpers import device_registry as dr
from homeassistant.helpers import entity_registry as er

from custom_components.docker_apps import (
    DOMAIN,
    async_remove_config_entry_device,
    config_flow,
    websocket,
)
from custom_components.docker_apps.catalog import CatalogError
from custom_components.docker_apps.diagnostics import (
    async_get_config_entry_diagnostics,
)


def entity_ids(hass, entry, domain: str) -> list[str]:
    return [
        e.entity_id
        for e in er.async_entries_for_config_entry(er.async_get(hass), entry.entry_id)
        if e.domain == domain
    ]


async def call(hass, name: str, response: bool = False, **data):
    return await hass.services.async_call(
        DOMAIN, name, data, blocking=True, return_response=response
    )


async def test__setup_should_add_host_and_app_entities(hass, entry):
    assert entry.state is ConfigEntryState.LOADED
    states = {s.entity_id: s for s in hass.states.async_all()}
    for domain in ("sensor", "switch", "button"):
        assert entity_ids(hass, entry, domain), domain
    status = next(
        states[i] for i in entity_ids(hass, entry, "sensor") if "immich_status" in i
    )
    assert status.state == "running"
    assert status.attributes["docker_app"] == "immich"

    assert await hass.config_entries.async_unload(entry.entry_id)
    assert entry.state is ConfigEntryState.NOT_LOADED


async def test__managed_app_should_update(hass, entry):
    await call(hass, "manage", app="immich", managed=True)
    (update_id,) = entity_ids(hass, entry, "update")
    state = hass.states.get(update_id)
    assert state.state == "on"
    assert state.attributes["latest_version"] == "v2.2.0"

    await hass.services.async_call(
        "update", "install", {"entity_id": update_id}, blocking=True
    )
    await call(hass, "check_updates")
    assert hass.states.get(update_id).attributes["installed_version"] == "v2.2.0"


async def test__app_entities_should_control_the_app(hass, entry):
    (switch_id,) = entity_ids(hass, entry, "switch")
    await hass.services.async_call(
        "switch", "turn_off", {"entity_id": switch_id}, blocking=True
    )
    assert hass.states.get(switch_id).state == "off"
    await hass.services.async_call(
        "switch", "turn_on", {"entity_id": switch_id}, blocking=True
    )
    for button_id in entity_ids(hass, entry, "button"):
        await hass.services.async_call(
            "button", "press", {"entity_id": button_id}, blocking=True
        )
    assert hass.states.get(switch_id).state == "on"


async def test__services(hass, entry):
    logs = await call(hass, "logs", response=True, app="immich")
    assert logs["logs"]
    await call(hass, "recreate", app="immich")
    await call(hass, "container_restart", container="immich_server")
    for kind in ("containers", "containers_apps", "images", "volumes"):
        assert "removed" in await call(hass, "prune", response=True, kind=kind)


async def test__websocket_commands(hass, entry):
    connection = MagicMock()
    for handler, msg in (
        (websocket.ws_state, {}),
        (websocket.ws_store, {}),
        (websocket.ws_resources, {}),
        (websocket.ws_app_environment, {"app": "immich"}),
        (websocket.ws_app_volumes, {"app": "immich"}),
    ):
        # async commands answer from a background task
        running = set(hass._background_tasks)
        handler(hass, connection, {"id": 1, **msg})
        await asyncio.gather(*(hass._background_tasks - running))
    assert connection.send_result.call_count == 5
    connection.send_error.assert_not_called()


async def test__diagnostics(hass, entry):
    diagnostics = await async_get_config_entry_diagnostics(hass, entry)
    assert diagnostics


async def test__config_flow_should_suggest_folders(hass, docker):
    flow = hass.config_entries.flow
    with patch.object(config_flow, "DockerClient", lambda url: docker):
        result = await flow.async_init(DOMAIN, context={"source": "user"})
        result = await flow.async_configure(
            result["flow_id"], {"docker_url": "unix:///var/run/docker.sock"}
        )
    assert result["step_id"] == "folders"
    assert result["data_schema"]({})["apps_root"].endswith("/apps")


async def test__options_flow(hass, entry):
    options = hass.config_entries.options
    result = await options.async_init(entry.entry_id)
    result = await options.async_configure(
        result["flow_id"],
        {
            "untracked_images": "redis, postgres",
            "apps_root": entry.data["apps_root"],
            "data_root": entry.data["data_root"],
            "read_only": True,
            "update_hours": 12,
        },
    )
    assert entry.options["untracked_images"] == ["redis", "postgres"]


async def test__more_services(hass, entry):
    await call(hass, "manage", app="immich", managed=True)
    await call(hass, "skip_update", app="immich", services=["redis"])
    updated = await call(
        hass, "update_services", response=True, app="immich", services=["redis"]
    )
    assert updated == {"updated": {"redis": "8.1"}}
    await call(hass, "check_updates", repositories=True)
    await call(hass, "container_stop", container="immich_server")
    for name, data in (
        ("recreate", {"app": "missing"}),
        ("container_start", {"container": "missing"}),
        ("logs", {}),
        ("install", {"store_app": "missing"}),
    ):
        with pytest.raises(ServiceValidationError):
            await call(hass, name, response=name in ("logs", "install"), **data)
    await call(hass, "uninstall", app="immich")
    assert "immich" not in entry.runtime_data.docker.data.apps


async def test__websocket_errors_and_logs(hass, entry):
    connection = MagicMock(subscriptions={})
    for handler, msg in (
        (websocket.ws_store_app, {"app_id": "missing"}),
        (websocket.ws_store_ports, {"app_id": "missing"}),
        (websocket.ws_app_environment, {"app": "missing"}),
        (websocket.ws_app_volumes, {"app": "missing"}),
    ):
        running = set(hass._background_tasks)
        handler(hass, connection, {"id": 1, **msg})
        await asyncio.gather(*(hass._background_tasks - running))
    assert connection.send_error.call_count == 4

    running = set(hass._background_tasks)
    websocket.ws_logs_subscribe(
        hass, connection, {"id": 2, "container": "immich_server", "tail": 10}
    )
    await asyncio.gather(*(hass._background_tasks - running))
    sent = [c.args[0]["event"] for c in connection.send_message.call_args_list]
    assert sent[-1] == {"end": True}


async def test__docker_events_should_refresh_and_fire(hass, entry, docker):
    fired = []
    hass.bus.async_listen("docker_apps_event", fired.append)
    container = next(iter(docker.containers.values()))
    docker.events.put_nowait(
        {
            "Action": "die",
            "Actor": {"ID": container["Id"], "Attributes": container["Labels"]},
        }
    )
    docker.events.put_nowait({"Action": "exec_start: sh"})
    await asyncio.sleep(0)
    await hass.async_block_till_done()
    assert [e.data["action"] for e in fired] == ["die"]


async def test__repository_flow_should_check_the_repository(hass, entry):
    flows = hass.config_entries.subentries
    result = await flows.async_init(
        (entry.entry_id, "repository"), context={"source": "user"}
    )
    result = await flows.async_configure(
        result["flow_id"], {"url": "https://example.com/apps"}
    )
    assert result["errors"] == {"kind": "unknown_kind"}
    with patch.object(
        config_flow, "async_fetch_repository", side_effect=CatalogError("down")
    ), patch.object(config_flow, "async_get_clientsession"):
        result = await flows.async_configure(
            result["flow_id"],
            {"url": "https://example.com/apps", "kind": "casaos"},
        )
    assert result["errors"] == {"base": "cannot_fetch"}


async def test__only_removed_app_devices_can_be_deleted(hass, entry):
    devices = dr.async_entries_for_config_entry(dr.async_get(hass), entry.entry_id)
    assert not any(
        [await async_remove_config_entry_device(hass, entry, d) for d in devices]
    )
