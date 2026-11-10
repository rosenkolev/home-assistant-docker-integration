import json
from types import SimpleNamespace

import pytest

from custom_components.docker_apps import _selfupdate
from custom_components.docker_apps import apps as apps_module
from custom_components.docker_apps._compose import ComposeEngine, load_model
from custom_components.docker_apps.apps import AppManager
from tests.fake_docker import FakeDocker, FakeRegistry
from tests.unit.test_apps import MemoryStore, fake_hass

COMPOSE = """services:
  homeassistant:
    container_name: homeassistant
    image: ha:{version}
    restart: unless-stopped
"""


@pytest.fixture
async def ha(tmp_path, monkeypatch):
    """A running Home Assistant 1 whose compose now asks for 2, with a backup of 1."""
    docker = FakeDocker()
    docker.add_image("ha:1")
    docker.add_image("ha:2")
    folder = tmp_path / "ha"
    backup = folder / ".docker-apps" / "history" / "1"
    backup.mkdir(parents=True)
    compose = folder / "compose.yaml"
    compose.write_text(COMPOSE.format(version="1"), encoding="utf-8")
    model = load_model(
        "ha", folder.as_posix(), {compose.as_posix(): compose.read_text()}, None
    )
    await ComposeEngine(docker).async_up(model)
    (backup / "compose.yaml").write_text(COMPOSE.format(version="1"), encoding="utf-8")
    compose.write_text(COMPOSE.format(version="2"), encoding="utf-8")

    monkeypatch.setattr(_selfupdate, "DockerClient", lambda url: docker)
    monkeypatch.setattr(_selfupdate, "START_DELAY", 0)
    monkeypatch.setattr(_selfupdate, "UP_STABLE", 0)
    monkeypatch.setattr(_selfupdate, "POLL", 0)
    monkeypatch.setattr(_selfupdate, "UP_TIMEOUT", 0)
    job = {
        "app": "ha",
        "project": "ha",
        "working_dir": folder.as_posix(),
        "files": [compose.as_posix()],
        "env_file": None,
        "service": "homeassistant",
        "docker_url": docker.url,
        "path_map": None,
        "backup": {
            "dir": backup.as_posix(),
            "files": [compose.as_posix()],
            "version": "1",
        },
    }
    return docker, job, compose


def running(docker):
    return {c["Names"][0]: (c["Image"], c["State"]) for c in docker.containers.values()}


async def test__helper_keeps_a_new_container_that_stays_up(ha):
    docker, job, compose = ha
    result = await _selfupdate._async_run(job)
    assert result["status"] == "updated"
    assert running(docker) == {"/homeassistant": ("ha:2", "running")}
    assert "ha:2" in compose.read_text()


async def test__helper_puts_back_the_old_container_when_the_new_one_crashes(
    ha, monkeypatch
):
    docker, job, compose = ha
    old = next(iter(docker.containers))
    action = docker.async_container_action

    async def new_one_crashes(id, name):
        await action(id, name)
        container = docker._get(id)
        if name == "start" and container["Image"] == "ha:2":
            container["State"] = "restarting"  # crash loop

    monkeypatch.setattr(docker, "async_container_action", new_one_crashes)
    result = await _selfupdate._async_run(job)

    assert result["status"] == "rolled_back"
    assert "did not stay up" in result["error"]
    assert result["files_restored"]
    assert running(docker) == {"/homeassistant": ("ha:1", "running")}
    assert old in docker.containers  # the same container, not a new one
    assert "ha:1" in compose.read_text()


async def test__helper_leaves_ha_running_when_the_new_one_cant_be_created(ha):
    docker, job, compose = ha
    compose.write_text(
        COMPOSE.format(version="2") + "    security_opt: [label=invalid]\n",
        encoding="utf-8",
    )
    result = await _selfupdate._async_run(job)
    assert result["status"] == "failed"
    assert "invalid --security-opt" in result["error"]
    assert running(docker) == {"/homeassistant": ("ha:1", "running")}
    assert "ha:1" in compose.read_text()


@pytest.fixture
async def manager(tmp_path, monkeypatch):
    monkeypatch.setattr(apps_module, "Store", MemoryStore)
    notes = []
    monkeypatch.setattr(
        apps_module,
        "persistent_notification",
        SimpleNamespace(async_create=lambda hass, text, **kw: notes.append(text)),
    )
    hass = fake_hass()
    hass.config.path = lambda name: (tmp_path / "config" / name).as_posix()
    manager = AppManager(hass, "entry", FakeDocker(), FakeRegistry(), "/apps", "/data")
    await manager.async_setup()
    manager.notes = notes
    return manager


async def test__setup_shows_a_rolled_back_update_and_fixes_the_history(
    manager, tmp_path
):
    backup = {"dir": "/apps/ha/.docker-apps/history/1", "files": [], "version": "1"}
    manager.settings["ha"] = apps_module.AppSettings(
        managed=True,
        store={"version": "2"},
        history=[{**backup, "store": {"version": "1"}}],
    )
    result = tmp_path / "config" / _selfupdate.RESULT_FILE
    result.parent.mkdir(parents=True)
    result.write_text(
        json.dumps(
            {
                "app": "ha",
                "status": "rolled_back",
                "error": "it restarting with exit code 1",
                "backup": backup,
                "files_restored": True,
            }
        ),
        encoding="utf-8",
    )

    assert await manager.async_check_self_update()
    assert not result.exists()
    assert "did not come up" in manager.notes[0]
    assert "put back to 1" in manager.notes[0]
    settings = manager.get("ha")
    assert settings.history == [] and settings.store == {"version": "1"}
    assert not await manager.async_check_self_update()  # shown once
