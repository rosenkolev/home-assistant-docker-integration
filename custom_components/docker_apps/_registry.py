"""Read-only container registry client (Docker Hub, ghcr.io, lscr.io, quay.io, ...)."""

from __future__ import annotations

import asyncio
import re
import time
from urllib.parse import quote

import aiohttp

from ._models import split_image

MANIFEST_ACCEPT = ", ".join(
    (
        "application/vnd.oci.image.index.v1+json",
        "application/vnd.docker.distribution.manifest.list.v2+json",
        "application/vnd.oci.image.manifest.v1+json",
        "application/vnd.docker.distribution.manifest.v2+json",
    )
)
TAGS_PAGE_SIZE = 1000
TAGS_MAX_PAGES = 10
CACHE_SECONDS = 3600
RETRY_SECONDS = 5
RETRY_MAX_SECONDS = 60
# registries that list tags by creation time, not by name (lscr.io is ghcr.io)
CREATION_ORDER_REGISTRIES = {"ghcr.io", "lscr.io"}
_AUTH_PARAM = re.compile(r'(\w+)="([^"]*)"')


class RegistryError(Exception):
    """Registry request failed."""


def _retry_after(value: str | None) -> float:
    try:
        seconds = float(value) if value else RETRY_SECONDS
    except ValueError:
        seconds = RETRY_SECONDS
    return min(max(seconds, 0), RETRY_MAX_SECONDS)


def parse_reference(image: str) -> tuple[str, str, str]:
    """Return (registry host, repository, tag or digest) for an image reference.

    A pinned `repo:tag@digest` gives the digest, and the repository without the tag.
    """
    image, tag, digest = split_image(image)
    reference = digest or tag or "latest"

    parts = image.split("/", 1)
    if len(parts) == 1 or not (
        "." in parts[0] or ":" in parts[0] or parts[0] == "localhost"
    ):
        registry = "registry-1.docker.io"
        repository = image if "/" in image else f"library/{image}"
    else:
        registry, repository = parts
    if registry in ("docker.io", "index.docker.io"):
        registry = "registry-1.docker.io"
    return registry, repository, reference


class RegistryClient:
    """Anonymous registry client with a small in-memory cache."""

    def __init__(self, session: aiohttp.ClientSession):
        self._session = session
        self._tokens: dict[tuple[str, str], tuple[str, float]] = {}
        self._cache: dict[tuple, tuple[object, float]] = {}

    def _cached(self, key: tuple):
        hit = self._cache.get(key)
        if hit and hit[1] > time.monotonic():
            return hit[0]
        return None

    def _store(self, key: tuple, value):
        self._cache[key] = (value, time.monotonic() + CACHE_SECONDS)
        return value

    def clear_cache(self) -> None:
        self._cache.clear()

    async def _auth_headers(
        self, registry: str, repository: str, challenge: str | None = None
    ) -> dict[str, str]:
        token = self._tokens.get((registry, repository))
        if token and token[1] > time.monotonic() and challenge is None:
            return {"Authorization": f"Bearer {token[0]}"}
        if challenge is None or not challenge.lower().startswith("bearer"):
            return {}

        params = dict(_AUTH_PARAM.findall(challenge))
        realm = params.pop("realm", None)
        if not realm:
            return {}
        params.setdefault("scope", f"repository:{repository}:pull")
        async with self._session.get(realm, params=params) as resp:
            if resp.status != 200:
                raise RegistryError(f"token request failed: {resp.status}")
            data = await resp.json(content_type=None)
        value = data.get("token") or data.get("access_token")
        expires = int(data.get("expires_in") or 300)
        self._tokens[(registry, repository)] = (value, time.monotonic() + expires - 30)
        return {"Authorization": f"Bearer {value}"}

    async def _get(
        self,
        registry: str,
        repository: str,
        path: str,
        headers: dict | None = None,
        method: str = "GET",
    ) -> tuple[dict, object]:
        url = (
            path
            if path.startswith("http")
            else f"https://{registry}/v2/{repository}/{path}"
        )
        request_headers = dict(headers or {})
        request_headers.update(await self._auth_headers(registry, repository))

        authorized = limited = False
        while True:
            delay = None
            async with self._session.request(
                method, url, headers=request_headers
            ) as resp:
                if resp.status == 401 and not authorized:
                    authorized = True
                    challenge = resp.headers.get("Www-Authenticate")
                    request_headers.update(
                        await self._auth_headers(registry, repository, challenge)
                    )
                    continue
                if resp.status == 429 and not limited:
                    # too many requests: wait as long as the registry asks, once
                    limited = True
                    delay = _retry_after(resp.headers.get("Retry-After"))
                elif resp.status >= 400:
                    raise RegistryError(f"{method} {url}: {resp.status}")
                else:
                    body = None
                    if method != "HEAD":
                        body = await resp.json(content_type=None)
                    return dict(resp.headers), body
            await asyncio.sleep(delay)

    async def async_digest(self, image: str) -> str | None:
        """Return the remote digest (index or manifest) of an image tag."""
        registry, repository, reference = parse_reference(image)
        key = ("digest", registry, repository, reference)
        if (hit := self._cached(key)) is not None:
            return hit
        headers, _ = await self._get(
            registry,
            repository,
            f"manifests/{reference}",
            {"Accept": MANIFEST_ACCEPT},
            method="HEAD",
        )
        return self._store(key, headers.get("Docker-Content-Digest"))

    async def async_tags(self, image: str) -> list[str]:
        """Return the tags of the image repository.

        ghcr.io lists tags by creation time, and some repositories have tens of
        thousands: there only the tags pushed after the image's tag are read.
        """
        registry, repository, _ = parse_reference(image)
        tag = split_image(image)[1]
        after = tag if registry in CREATION_ORDER_REGISTRIES else None
        key = ("tags", registry, repository, after)
        if (hit := self._cached(key)) is not None:
            return hit

        tags: list[str] = []
        path = f"tags/list?n={TAGS_PAGE_SIZE}"
        if after:
            path += f"&last={quote(after)}"
        for _ in range(TAGS_MAX_PAGES):
            headers, body = await self._get(registry, repository, path)
            tags.extend((body or {}).get("tags") or [])
            link = headers.get("Link")
            match = re.search(r"<([^>]+)>", link) if link else None
            if not match:
                break
            next_path = match.group(1)
            path = (
                f"https://{registry}{next_path}"
                if next_path.startswith("/")
                else next_path
            )
        return self._store(key, tags)
