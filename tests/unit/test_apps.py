"""AppManager with a fake Docker engine, a fake registry and real files in tmp_path."""

# cspell:words btrfs resolv

from types import SimpleNamespace

import pytest

from custom_components.docker_apps import apps as apps_module
from custom_components.docker_apps._compose import load_model
from custom_components.docker_apps.apps import AppError, AppManager, own_container_id
from tests.fake_docker import FakeDocker, FakeRegistry

COMPOSE = """\
name: immich
services:
  immich-server:
    container_name: immich_server
    # keep this comment
    image: ghcr.io/immich-app/immich-server:${IMMICH_VERSION:-release}
    ports:
      - "2283:2283"
    depends_on:
      - redis
      - database
  immich-machine-learning:
    image: ghcr.io/immich-app/immich-machine-learning:${IMMICH_VERSION:-release}-rknn
  redis:
    image: docker.io/valkey/valkey:8.0
  database:
    image: postgres:16.4
"""
ENV = "IMMICH_VERSION=v2.1.0\nDB_PASSWORD=secret\n"

TAGS = {
    "ghcr.io/immich-app/immich-server": ["v2.0.0", "v2.1.0", "v2.2.0", "release"],
    "ghcr.io/immich-app/immich-machine-learning": [
        "v2.1.0-rknn",
        "v2.2.0-rknn",
        "v2.2.0",
    ],
    "docker.io/valkey/valkey": ["8.0", "8.1"],
    "postgres": ["16.4", "16.6", "17.0"],
}


class MemoryStore:
    def __init__(self, *args, **kwargs):
        self.data = None

    async def async_load(self):
        return self.data

    async def async_save(self, data):
        self.data = data


class FakeResponse:
    def __init__(self, status, text):
        self.status = status
        self._text = text

    async def text(self):
        return self._text

    async def __aenter__(self):
        return self

    async def __aexit__(self, *args):
        return False


class FakeHttp:
    def __init__(self, pages: dict[str, str]):
        self.pages = pages

    def get(self, url, **kwargs):
        return (
            FakeResponse(200, self.pages[url])
            if url in self.pages
            else FakeResponse(404, "")
        )


def tracked(manager, data):
    return {
        s
        for s, (_, is_tracked) in manager.service_images("immich", data).items()
        if is_tracked
    }


def fake_hass():
    async def run(func, *args):
        return func(*args)

    return SimpleNamespace(
        async_add_executor_job=run, config=SimpleNamespace(time_zone="Europe/Sofia")
    )


@pytest.fixture
def project(tmp_path):
    folder = tmp_path / "immich"
    folder.mkdir()
    (folder / "compose.yaml").write_text(COMPOSE, encoding="utf-8", newline="\n")
    (folder / ".env").write_text(ENV, encoding="utf-8", newline="\n")
    return folder.as_posix()


@pytest.fixture
async def setup(project, monkeypatch):
    monkeypatch.setattr(apps_module, "Store", MemoryStore)
    docker = FakeDocker()
    manager = AppManager(
        fake_hass(),
        "entry",
        docker,
        FakeRegistry(TAGS),
        "/apps",
        "/data",
        http=FakeHttp({}),
    )
    await manager.async_setup()
    model = load_model(
        "immich",
        project,
        {f"{project}/compose.yaml": COMPOSE},
        ENV,
        f"{project}/.env",
    )
    await manager.engine.async_up(model)
    data = await docker.async_fetch()
    return manager, docker, data


async def test__untracked_images_should_default_to_databases(setup):
    manager, _, data = setup
    assert tracked(manager, data) == {"immich-server", "immich-machine-learning"}


async def test__untracked_images_can_be_extended(setup):
    manager, _, data = setup
    manager.untracked = [*manager.untracked, "machine-learning"]
    assert tracked(manager, data) == {"immich-server"}


async def test__per_app_services_override_the_global_list(setup):
    manager, _, data = setup
    await manager.async_configure(
        "immich", managed=True, services=["immich-server", "redis"]
    )
    assert tracked(manager, data) == {"immich-server", "redis"}


async def test__check_updates_only_for_managed_apps(setup):
    manager, _, data = setup
    assert await manager.async_check_updates(data, {}) == {}

    await manager.async_configure("immich", managed=True)
    update = (await manager.async_check_updates(data, {}))["immich"]
    assert update.installed_version == "v2.1.0"
    assert update.latest_version == "v2.2.0"
    assert update.has_update
    assert {s.service for s in update.targets} == {
        "immich-server",
        "immich-machine-learning",
    }
    redis = next(s for s in update.services if s.service == "redis")
    assert not redis.tracked and redis.latest == "8.1"


async def test__update_should_edit_env_once_back_up_and_recreate(setup, project):
    manager, docker, data = setup
    await manager.async_configure("immich", managed=True)
    update = (await manager.async_check_updates(data, {}))["immich"]
    docker.calls.clear()

    await manager.async_update(data.apps["immich"], update, data)

    env = open(f"{project}/.env", encoding="utf-8").read()
    assert "IMMICH_VERSION=v2.2.0\n" in env
    assert "DB_PASSWORD=secret" in env
    assert open(f"{project}/compose.yaml", encoding="utf-8").read() == COMPOSE
    assert ("pull", "ghcr.io/immich-app/immich-server:v2.2.0") in docker.calls
    assert ("create", "immich_server") in docker.calls
    assert ("create", "immich-redis-1") not in docker.calls
    history = manager.get("immich").history
    assert history[0]["version"] == "v2.1.0"
    assert (
        "IMMICH_VERSION=v2.1.0"
        in open(f"{history[0]['dir']}/.env", encoding="utf-8").read()
    )


async def test__rollback_should_restore_files(setup, project):
    manager, docker, data = setup
    await manager.async_configure("immich", managed=True)
    update = (await manager.async_check_updates(data, {}))["immich"]
    await manager.async_update(data.apps["immich"], update, data)

    data = await docker.async_fetch()
    await manager.async_rollback(data.apps["immich"], "v2.1.0", data)
    assert "IMMICH_VERSION=v2.1.0\n" in open(f"{project}/.env", encoding="utf-8").read()
    assert manager.get("immich").history == []


async def test__failed_health_should_roll_back(setup, project, monkeypatch):
    manager, docker, data = setup
    await manager.async_configure("immich", managed=True)
    update = (await manager.async_check_updates(data, {}))["immich"]

    async def unhealthy(_project):
        raise AppError("immich-server is unhealthy")

    monkeypatch.setattr(manager, "_async_wait_healthy", unhealthy)
    with pytest.raises(AppError, match="rolled back"):
        await manager.async_update(data.apps["immich"], update, data)
    assert "IMMICH_VERSION=v2.1.0\n" in open(f"{project}/.env", encoding="utf-8").read()


async def test__update_single_untracked_service(setup, project):
    manager, _, data = setup
    changes = await manager.async_update_services(
        data.apps["immich"], ["redis"], None, data
    )
    assert changes == {"redis": "8.1"}
    assert (
        "image: docker.io/valkey/valkey:8.1\n"
        in open(f"{project}/compose.yaml", encoding="utf-8").read()
    )
    assert (
        "# keep this comment"
        in open(f"{project}/compose.yaml", encoding="utf-8").read()
    )


UPSTREAM = "https://example.com/{version}/docker-compose.yml"


async def test__upstream_should_include_required_service(setup):
    manager, _, data = setup
    manager.http = FakeHttp(
        {
            "https://example.com/v2.2.0/docker-compose.yml": (
                "services:\n  immich-server:\n    image: ghcr.io/immich-app/immich-server:${IMMICH_VERSION}\n"
                "  redis:\n    image: docker.io/valkey/valkey:8.1\n  database:\n    image: postgres:16.4\n"
            )
        }
    )
    await manager.async_configure("immich", managed=True, upstream=UPSTREAM)
    update = (await manager.async_check_updates(data, {}))["immich"]
    redis = next(s for s in update.services if s.service == "redis")
    assert redis.required and redis.latest == "8.1"
    assert "redis" in {s.service for s in update.targets}
    assert update.blocked is None


async def test__upstream_database_major_should_hold_the_update(setup):
    manager, _, data = setup
    manager.http = FakeHttp(
        {
            "https://example.com/v2.2.0/docker-compose.yml": "services:\n  database:\n    image: postgres:17.0\n"
        }
    )
    await manager.async_configure(
        "immich", managed=True, upstream=UPSTREAM, allow_db_minor=True
    )
    update = (await manager.async_check_updates(data, {}))["immich"]
    assert update.blocked and "postgres:17.0" in update.blocked
    assert update.has_update
    with pytest.raises(AppError, match="database upgrade"):
        await manager.async_update(data.apps["immich"], update, data)


async def test__upstream_database_minor_needs_permission(setup):
    manager, _, data = setup
    manager.http = FakeHttp(
        {
            "https://example.com/v2.2.0/docker-compose.yml": "services:\n  database:\n    image: postgres:16.6\n"
        }
    )
    await manager.async_configure("immich", managed=True, upstream=UPSTREAM)
    assert (await manager.async_check_updates(data, {}))["immich"].blocked

    await manager.async_configure("immich", allow_db_minor=True)
    update = (await manager.async_check_updates(data, {}))["immich"]
    assert update.blocked is None
    assert "database" in {s.service for s in update.targets}


async def test__stop_is_refused_for_own_app(setup):
    manager, _, data = setup
    manager.self_container_id = data.app_containers("immich")[0].id
    with pytest.raises(AppError):
        await manager.async_app_action("immich", data, "stop")


async def test__files_outside_mounts_are_refused(setup):
    manager, _, data = setup
    from custom_components.docker_apps._paths import PathMap

    manager.paths = PathMap([("/somewhere", "/elsewhere")])
    assert not manager.can_edit(data.apps["immich"])
    with pytest.raises(AppError, match="not mounted"):
        manager.local_path(data.apps["immich"].working_dir)


ID = "73d3d60faa44735897cbf1c0ac1bf78116aa1e9eb88532e59b4055e3aa9fae34"


@pytest.mark.parametrize(
    "root",
    [
        "/var/lib/docker/containers",  # default data root
        "/srv/docker-engine/containers",  # custom data root
    ],
)
def test__own_container_id_should_find_the_id(tmp_path, root):
    mountinfo = tmp_path / "mountinfo"
    mountinfo.write_text(
        f"2088 2076 0:38 {root}/{ID}/resolv.conf /etc/resolv.conf rw - btrfs x rw\n"
        f"2089 2076 0:38 {root}/{ID}/hostname /etc/hostname rw - btrfs x rw\n",
        encoding="utf-8",
    )
    assert own_container_id(str(mountinfo)) == ID


def test__own_container_id_outside_a_container(tmp_path):
    mountinfo = tmp_path / "mountinfo"
    mountinfo.write_text("22 1 0:21 / /proc rw - proc proc rw\n", encoding="utf-8")
    assert own_container_id(str(mountinfo)) is None
    assert own_container_id(str(tmp_path / "missing")) is None
