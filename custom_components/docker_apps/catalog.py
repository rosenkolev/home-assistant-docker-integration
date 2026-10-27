"""Store repositories: download, cache and look up store apps."""

from __future__ import annotations

import tarfile
import zipfile
from dataclasses import dataclass

import aiohttp
from homeassistant.core import HomeAssistant
from homeassistant.helpers.storage import Store

from ._store import (
    StoreApp,
    archive_url,
    parse_archive,
    raw_file_url,
    repo_id_for,
)
from .const import _LOGGER, DOMAIN

STORAGE_VERSION = 1
# bump when parsing changes, so cached catalogs are downloaded again
CATALOG_FORMAT = 3
MAX_ARCHIVE = 200 * 1024 * 1024
TIMEOUT = aiohttp.ClientTimeout(total=300)


@dataclass
class Repository:
    url: str
    kind: str
    name: str

    @property
    def id(self) -> str:
        return repo_id_for(self.url)


class CatalogError(Exception):
    """A repository can't be read."""


async def async_fetch_repository(
    session: aiohttp.ClientSession,
    hass: HomeAssistant,
    repository: Repository,
    etag: str | None = None,
) -> tuple[list[StoreApp] | None, str | None]:
    """Return (apps, etag); apps is None when the repository didn't change."""
    headers = {"If-None-Match": etag} if etag else {}
    download = archive_url(repository.url)
    async with session.get(download, headers=headers, timeout=TIMEOUT) as resp:
        if resp.status == 304:
            return None, etag
        if resp.status >= 400:
            raise CatalogError(f"{download}: HTTP {resp.status}")
        raw = bytearray()
        async for chunk in resp.content.iter_chunked(1024 * 1024):
            raw += chunk
            if len(raw) > MAX_ARCHIVE:
                raise CatalogError(f"{download}: archive too big")
        new_etag = resp.headers.get("ETag")
    try:
        apps = await hass.async_add_executor_job(
            parse_archive,
            bytes(raw),
            repository.kind,
            repository.id,
            raw_file_url(repository.url),
        )
    except (EOFError, tarfile.TarError, zipfile.BadZipFile) as err:
        raise CatalogError(f"{download}: broken archive: {err}") from err
    return apps, new_etag


class Catalog:
    """All apps of the configured repositories, cached in storage."""

    def __init__(
        self, hass: HomeAssistant, entry_id: str, session: aiohttp.ClientSession
    ):
        self.hass = hass
        self.session = session
        self.repositories: dict[str, Repository] = {}
        self.apps: dict[str, StoreApp] = {}
        self.errors: dict[str, str] = {}
        self._etags: dict[str, str | None] = {}
        self._store = Store(hass, STORAGE_VERSION, f"{DOMAIN}.{entry_id}.catalog")

    async def async_load(self, repositories: list[Repository]) -> None:
        self.repositories = {r.id: r for r in repositories}
        self.apps = {}
        self._etags = {}
        self.errors = {k: v for k, v in self.errors.items() if k in self.repositories}
        data = await self._store.async_load() or {}
        if data.get("format") != CATALOG_FORMAT:
            data = {}
        for repo_id, cached in (data.get("repositories") or {}).items():
            if repo_id not in self.repositories:
                continue
            self._etags[repo_id] = cached.get("etag")
            for raw in cached.get("apps") or []:
                try:
                    app = StoreApp.from_dict(raw)
                except TypeError:
                    continue
                self.apps[app.id] = app

    async def _async_save(self) -> None:
        repositories = {}
        for repo_id in self.repositories:
            repositories[repo_id] = {
                "etag": self._etags.get(repo_id),
                "apps": [
                    a.as_dict() for a in self.apps.values() if a.repo_id == repo_id
                ],
            }
        await self._store.async_save(
            {"format": CATALOG_FORMAT, "repositories": repositories}
        )

    async def async_refresh(self) -> None:
        changed = False
        for repo_id, repository in self.repositories.items():
            try:
                apps, new_etag = await async_fetch_repository(
                    self.session, self.hass, repository, self._etags.get(repo_id)
                )
            except (CatalogError, aiohttp.ClientError, TimeoutError, OSError) as err:
                _LOGGER.warning("Repository %s failed: %s", repository.url, err)
                self.errors[repo_id] = str(err)
                continue
            self.errors.pop(repo_id, None)
            if apps is None:
                continue
            self.apps = {k: v for k, v in self.apps.items() if v.repo_id != repo_id}
            self.apps.update({a.id: a for a in apps})
            self._etags[repo_id] = new_etag
            changed = True
            _LOGGER.debug("Repository %s: %s apps", repository.url, len(apps))
        if changed:
            await self._async_save()

    def get(self, app_id: str) -> StoreApp:
        if app := self.apps.get(app_id):
            return app
        raise CatalogError(f"app {app_id} not found")
