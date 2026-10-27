"""Catalog cache: apps follow the configured repositories."""

import io
import tarfile

import pytest

from custom_components.docker_apps import catalog as catalog_module
from custom_components.docker_apps.catalog import (
    CATALOG_FORMAT,
    Catalog,
    CatalogError,
    Repository,
    async_fetch_repository,
)
from custom_components.docker_apps._store import StoreApp
from tests.unit.test_apps import MemoryStore

CASAOS = Repository("https://github.com/IceWhaleTech/CasaOS-AppStore", "casaos", "A")
TIPI = Repository("https://github.com/runtipi/runtipi-appstore", "runtipi", "B")


def app(repository: Repository, app_id: str) -> dict:
    return StoreApp(
        id=f"{repository.id}.{app_id}",
        repo_id=repository.id,
        app_id=app_id,
        kind=repository.kind,
        name=app_id,
        version="1",
        package_version="1",
    ).as_dict()


async def test__removed_repository_drops_its_apps(monkeypatch):
    monkeypatch.setattr(catalog_module, "Store", MemoryStore)
    catalog = Catalog(None, "entry", None)
    catalog._store.data = {
        "format": CATALOG_FORMAT,
        "repositories": {
            CASAOS.id: {"etag": "a", "apps": [app(CASAOS, "jellyfin")]},
            TIPI.id: {"etag": "b", "apps": [app(TIPI, "whoami")]},
        },
    }

    await catalog.async_load([CASAOS, TIPI])
    assert len(catalog.apps) == 2

    await catalog.async_load([CASAOS])
    assert list(catalog.apps) == [f"{CASAOS.id}.jellyfin"]


COMPOSE = b"""name: whoami
services:
  whoami:
    image: traefik/whoami:v1.10
x-casaos:
  title: {en_US: Whoami}
"""


def casaos_archive() -> bytes:
    buffer = io.BytesIO()
    with tarfile.open(fileobj=buffer, mode="w:gz") as archive:
        info = tarfile.TarInfo("store-main/Apps/whoami/docker-compose.yml")
        info.size = len(COMPOSE)
        archive.addfile(info, io.BytesIO(COMPOSE))
    return buffer.getvalue()


class FakeResponse:
    """An HTTP response whose body arrives in small chunks, like a real download."""

    def __init__(self, body: bytes):
        self.status = 200
        self.headers = {}
        self.content = self
        self._body = body

    async def iter_chunked(self, size):
        for start in range(0, len(self._body), 100):
            yield self._body[start : start + 100]

    async def __aenter__(self):
        return self

    async def __aexit__(self, *args):
        return None


class FakeSession:
    def __init__(self, body: bytes):
        self.body = body

    def get(self, url, **kwargs):
        return FakeResponse(self.body)


class FakeHass:
    async def async_add_executor_job(self, func, *args):
        return func(*args)


async def test__archive_is_read_in_full():
    apps, _ = await async_fetch_repository(
        FakeSession(casaos_archive()), FakeHass(), CASAOS
    )
    assert [a.app_id for a in apps] == ["whoami"]


async def test__broken_archive_is_a_repository_error():
    with pytest.raises(CatalogError, match="broken archive"):
        await async_fetch_repository(
            FakeSession(casaos_archive()[:-40]), FakeHass(), CASAOS
        )
