"""Catalog cache: apps follow the configured repositories."""

from custom_components.docker_apps import catalog as catalog_module
from custom_components.docker_apps.catalog import CATALOG_FORMAT, Catalog, Repository
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
