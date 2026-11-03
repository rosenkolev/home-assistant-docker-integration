"""A real Home Assistant core (no HTTP server) running Docker Apps on FakeDocker."""

from unittest.mock import AsyncMock, patch

import pytest
from homeassistant import bootstrap, loader
from homeassistant.config_entries import ConfigEntries, ConfigEntry
from homeassistant.core import HomeAssistant

import custom_components.docker_apps as integration
from custom_components.docker_apps._compose import ComposeEngine, load_model
from tests.fake_docker import FakeDocker, FakeRegistry
from tests.unit.test_apps import COMPOSE, ENV, TAGS


@pytest.fixture
async def hass(tmp_path):
    hass = HomeAssistant(str(tmp_path / "config"))
    loader.async_setup(hass)
    hass.config_entries = ConfigEntries(hass, {})
    await bootstrap.async_load_base_functionality(hass)
    # the panel and the static files need the HTTP server, which doesn't run here
    hass.config.components.update({"http", "frontend", "panel_custom", "websocket_api"})
    hass.http = AsyncMock()
    yield hass
    await hass.async_stop(force=True)


@pytest.fixture
async def docker(tmp_path):
    """Docker with the Immich app of the unit tests, from its compose folder."""
    folder = tmp_path / "apps" / "immich"
    folder.mkdir(parents=True)
    (folder / "compose.yaml").write_text(COMPOSE, encoding="utf-8", newline="\n")
    (folder / ".env").write_text(ENV, encoding="utf-8", newline="\n")
    path = folder.as_posix()
    model = load_model("immich", path, {f"{path}/compose.yaml": COMPOSE}, ENV)
    docker = FakeDocker()
    await ComposeEngine(docker).async_up(model)
    return docker


@pytest.fixture
async def entry(hass, docker, tmp_path):
    with (
        patch.object(integration, "DockerClient", lambda url: docker),
        patch.object(integration, "RegistryClient", lambda session: FakeRegistry(TAGS)),
        patch.object(integration, "async_get_clientsession"),
        patch.object(integration, "async_register_panel", AsyncMock()),
        patch.object(integration, "add_extra_js_url"),
        patch(
            "custom_components.docker_apps.entity.get_url",
            return_value="http://ha.local:8123",
        ),
    ):
        entry = ConfigEntry(
            domain=integration.DOMAIN,
            title="Docker",
            data={
                "apps_root": (tmp_path / "apps").as_posix(),
                "data_root": (tmp_path / "data").as_posix(),
            },
            options={},
            source="user",
            version=1,
            minor_version=1,
            unique_id=None,
            discovery_keys={},
            subentries_data=None,
        )
        await hass.config_entries.async_add(entry)
        await hass.async_block_till_done()
        yield entry
