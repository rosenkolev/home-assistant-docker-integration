"""Install and package-update a store app into tmp_path with a fake engine."""

import json

import pytest

from custom_components.docker_apps import apps as apps_module
from custom_components.docker_apps._docker_client import DockerError
from custom_components.docker_apps._store import parse_runtipi
from custom_components.docker_apps.apps import AppError, AppManager
from tests.fake_docker import FakeDocker, FakeRegistry
from tests.unit.test_apps import MemoryStore, fake_hass

CONFIG = {
    "name": "Whoami",
    "id": "whoami",
    "port": 8099,
    "tipi_version": 3,
    "version": "1.10.0",
    "form_fields": [
        {"type": "random", "label": "SECRET", "env_variable": "SECRET", "min": 20}
    ],
}
COMPOSE = """
services:
  whoami:
    image: traefik/whoami:v1.10.0
    ports:
      - ${APP_PORT}:80
    environment:
      SECRET: ${SECRET}
    volumes:
      - ${APP_DATA_DIR}/data:/data
"""


def store_app(version: str, tipi: int):
    config = {**CONFIG, "version": version, "tipi_version": tipi}
    return parse_runtipi(
        json.dumps(config), COMPOSE.replace("v1.10.0", f"v{version}"), None, "tipi"
    )


@pytest.fixture
async def manager(tmp_path, monkeypatch):
    monkeypatch.setattr(apps_module, "Store", MemoryStore)
    docker = FakeDocker()
    root = tmp_path.as_posix()
    manager = AppManager(
        fake_hass(), "entry", docker, FakeRegistry(), f"{root}/apps", f"{root}/data"
    )
    await manager.async_setup()
    return manager


async def test__install_writes_files_and_starts_containers(manager, tmp_path):
    data = await manager.client.async_fetch()
    key = await manager.async_install_store_app(store_app("1.10.0", 3), data=data)

    assert key == "whoami"
    folder = tmp_path / "apps" / "whoami"
    env = (folder / ".env").read_text(encoding="utf-8")
    assert "APP_PORT=8099" in env
    secret = next(
        line.split("=", 1)[1] for line in env.splitlines() if line.startswith("SECRET=")
    )
    assert len(secret) >= 20
    assert (folder / "compose.override.yaml").exists()
    assert (folder / ".docker-apps" / "manifest.json").exists()

    container = next(iter(manager.client.containers.values()))
    assert container["State"] == "running"
    assert container["Labels"]["com.docker.compose.project"] == "whoami"
    settings = manager.get("whoami")
    assert settings.managed and settings.store["package_version"] == "3"


async def test__package_update_keeps_secrets_and_override(manager, tmp_path):
    data = await manager.client.async_fetch()
    await manager.async_install_store_app(store_app("1.10.0", 3), data=data)
    folder = tmp_path / "apps" / "whoami"
    before = (folder / ".env").read_text(encoding="utf-8")
    (folder / "compose.override.yaml").write_text(
        "services:\n  whoami:\n    restart: always\n", encoding="utf-8"
    )

    data = await manager.client.async_fetch()
    new = store_app("1.11.0", 4)
    update = (await manager.async_check_updates(data, {new.id: new}))["whoami"]
    assert update.has_update and update.latest_version == "1.11.0"

    await manager.async_update(data.apps["whoami"], update, data)
    after = (folder / ".env").read_text(encoding="utf-8")

    def secret(text):
        return [x for x in text.splitlines() if x.startswith("SECRET=")]

    assert secret(before) == secret(after)
    assert "restart: always" in (folder / "compose.override.yaml").read_text(
        encoding="utf-8"
    )
    assert "traefik/whoami:v1.11.0" in (folder / "compose.yaml").read_text(
        encoding="utf-8"
    )
    container = next(iter(manager.client.containers.values()))
    assert container["Image"] == "traefik/whoami:v1.11.0"
    assert container["HostConfig"]["RestartPolicy"] == {"Name": "always"}
    assert manager.get("whoami").history[0]["version"] == "1.10.0"


async def test__install_refuses_port_conflicts(manager):
    manager.client.add_container(
        "other", "nginx", ports=[{"PrivatePort": 80, "PublicPort": 8099, "Type": "tcp"}]
    )
    data = await manager.client.async_fetch()
    with pytest.raises(AppError, match="8099"):
        await manager.async_install_store_app(store_app("1.10.0", 3), data=data)


async def test__failed_install_leaves_nothing_behind(manager, tmp_path, monkeypatch):
    docker = manager.client
    action = docker.async_container_action

    async def start_fails(id, name):
        if name == "start":
            raise DockerError(500, "port is already allocated")
        await action(id, name)

    monkeypatch.setattr(docker, "async_container_action", start_fails)
    data = await docker.async_fetch()
    with pytest.raises(AppError, match="already allocated"):
        await manager.async_install_store_app(store_app("1.10.0", 3), data=data)

    assert not docker.containers
    assert not (tmp_path / "apps" / "whoami").exists()
    assert "whoami" not in manager.settings


async def test__uninstall_with_data_removes_folders(manager, tmp_path):
    data = await manager.client.async_fetch()
    await manager.async_install_store_app(store_app("1.10.0", 3), data=data)
    (tmp_path / "data" / "whoami").mkdir(parents=True)
    data = await manager.client.async_fetch()

    await manager.async_uninstall(data.apps["whoami"], data, remove_data=True)

    assert not manager.client.containers
    assert not (tmp_path / "apps" / "whoami").exists()
    assert not (tmp_path / "data" / "whoami").exists()
    assert manager.get("whoami").store is None


async def test__uninstall_refuses_to_delete_unmounted_data(manager, tmp_path):
    from custom_components.docker_apps._paths import PathMap

    data = await manager.client.async_fetch()
    await manager.async_install_store_app(store_app("1.10.0", 3), data=data)
    data = await manager.client.async_fetch()
    apps = tmp_path.as_posix() + "/apps"
    manager.paths = PathMap([(apps, apps)])  # the data folder isn't mounted

    with pytest.raises(AppError, match="not mounted"):
        await manager.async_uninstall(data.apps["whoami"], data, remove_data=True)
    assert manager.client.containers  # nothing was removed


async def test__launcher_is_on_for_store_apps_and_off_for_found_ones(manager):
    data = await manager.client.async_fetch()
    await manager.async_install_store_app(store_app("1.10.0", 3), data=data)
    assert manager.in_launcher("whoami")
    assert not manager.in_launcher("found-project")

    await manager.async_configure("found-project", launcher=True)
    await manager.async_configure("whoami", launcher=False)
    assert manager.in_launcher("found-project")
    assert not manager.in_launcher("whoami")

    await manager.async_configure("found-project", launcher=None)
    assert "found-project" not in manager.settings  # back to defaults: nothing stored
