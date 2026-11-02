from __future__ import annotations

import pytest

from custom_components.docker_apps import _registry
from custom_components.docker_apps._registry import RegistryClient, RegistryError


class FakeResponse:
    def __init__(self, status: int, body=None, headers: dict | None = None):
        self.status = status
        self.body = body
        self.headers = headers or {}

    async def __aenter__(self):
        return self

    async def __aexit__(self, *args):
        return False

    async def json(self, content_type=None):
        return self.body


class FakeSession:
    """Answers requests in order and records the URLs."""

    def __init__(self, *responses: FakeResponse):
        self.responses = list(responses)
        self.urls: list[str] = []

    def request(self, method, url, headers=None):
        self.urls.append(url)
        return self.responses.pop(0)


@pytest.fixture
def sleeps(monkeypatch):
    waited: list[float] = []

    async def sleep(seconds):
        waited.append(seconds)

    monkeypatch.setattr(_registry.asyncio, "sleep", sleep)
    return waited


async def test__tags_should_start_after_current_tag_on_ghcr():
    session = FakeSession(FakeResponse(200, {"tags": ["v3.4.0", "commit-abc"]}))

    tags = await RegistryClient(session).async_tags("ghcr.io/immich-app/server:v3.3.3")

    assert tags == ["v3.4.0", "commit-abc"]
    assert session.urls == [
        "https://ghcr.io/v2/immich-app/server/tags/list?n=1000&last=v3.3.3"
    ]


async def test__tags_should_list_all_on_other_registries():
    session = FakeSession(FakeResponse(200, {"tags": ["16.1", "16.2"]}))

    await RegistryClient(session).async_tags("postgres:16.1")

    assert session.urls == [
        "https://registry-1.docker.io/v2/library/postgres/tags/list?n=1000"
    ]


async def test__tags_should_follow_next_page_link():
    session = FakeSession(
        FakeResponse(
            200,
            {"tags": ["a1"]},
            {"Link": '</v2/x/y/tags/list?last=a1&n=1000>; rel="next"'},
        ),
        FakeResponse(200, {"tags": ["a2"]}),
    )

    tags = await RegistryClient(session).async_tags("ghcr.io/x/y:a0")

    assert tags == ["a1", "a2"]
    assert session.urls[1] == "https://ghcr.io/v2/x/y/tags/list?last=a1&n=1000"


async def test__too_many_requests_should_wait_and_retry_once(sleeps):
    session = FakeSession(
        FakeResponse(429, headers={"Retry-After": "7"}),
        FakeResponse(200, {"tags": ["v2"]}),
    )

    tags = await RegistryClient(session).async_tags("ghcr.io/x/y:v1")

    assert tags == ["v2"]
    assert sleeps == [7]


async def test__too_many_requests_twice_should_fail(sleeps):
    session = FakeSession(FakeResponse(429), FakeResponse(429))

    with pytest.raises(RegistryError, match="429"):
        await RegistryClient(session).async_tags("ghcr.io/x/y:v1")
    assert sleeps == [_registry.RETRY_SECONDS]
