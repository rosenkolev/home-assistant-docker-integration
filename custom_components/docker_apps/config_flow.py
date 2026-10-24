"""Config flow: Docker connection, folders, options and store repositories."""

from __future__ import annotations

import posixpath
from typing import Any

import voluptuous as vol
from homeassistant.config_entries import (
    ConfigEntry,
    ConfigFlow,
    ConfigFlowResult,
    ConfigSubentryFlow,
    OptionsFlow,
    SubentryFlowResult,
)
from homeassistant.core import callback
from homeassistant.helpers.aiohttp_client import async_get_clientsession
from homeassistant.helpers.selector import (
    NumberSelector,
    NumberSelectorConfig,
    NumberSelectorMode,
    SelectOptionDict,
    SelectSelector,
    SelectSelectorConfig,
    SelectSelectorMode,
)

from ._docker_client import DockerClient, DockerError
from ._models import LABEL_WORKING_DIR
from ._store import KIND_CASAOS, KIND_RUNTIPI, KINDS
from .apps import DEFAULT_UNTRACKED
from .catalog import CatalogError, Repository, async_fetch_repository
from .const import (
    CONF_APPS_ROOT,
    CONF_DATA_ROOT,
    CONF_DOCKER_URL,
    CONF_READ_ONLY,
    CONF_REPO_KIND,
    CONF_REPO_URL,
    CONF_UNTRACKED,
    CONF_UPDATE_HOURS,
    DEFAULT_APPS_ROOT,
    DEFAULT_DATA_ROOT,
    DEFAULT_DOCKER_URL,
    DEFAULT_NAME,
    DEFAULT_UPDATE_HOURS,
    DOMAIN,
    KNOWN_REPOSITORIES,
    SUBENTRY_REPOSITORY,
)


def suggest_folders(containers: list[dict]) -> tuple[str, str]:
    """Guess the apps root and data root from the running compose projects."""
    dirs = sorted(
        {
            (c.get("Labels") or {}).get(LABEL_WORKING_DIR)
            for c in containers
            if (c.get("Labels") or {}).get(LABEL_WORKING_DIR)
        }
    )
    if not dirs:
        return DEFAULT_APPS_ROOT, DEFAULT_DATA_ROOT
    parents = {posixpath.dirname(d.rstrip("/")) for d in dirs}
    apps_root = parents.pop() if len(parents) == 1 else posixpath.commonpath(dirs)
    if apps_root in ("", "/"):
        return DEFAULT_APPS_ROOT, DEFAULT_DATA_ROOT

    sibling = posixpath.join(posixpath.dirname(apps_root), "volumes")
    sources = {
        m.get("Source", "")
        for c in containers
        for m in c.get("Mounts") or []
        if m.get("Type") == "bind"
    }
    data_root = (
        sibling
        if any(s.startswith(sibling + "/") for s in sources)
        else f"{apps_root.rstrip('/')}-data"
    )
    return apps_root, data_root


def detect_kind(url: str) -> str | None:
    lower = url.lower()
    if url in KNOWN_REPOSITORIES:
        return KNOWN_REPOSITORIES[url][0]
    if "tipi" in lower:
        return KIND_RUNTIPI
    if "casaos" in lower:
        return KIND_CASAOS
    return None


class DockerAppsConfigFlow(ConfigFlow, domain=DOMAIN):
    VERSION = 1

    def __init__(self) -> None:
        self._data: dict[str, Any] = {}

    @staticmethod
    @callback
    def async_get_options_flow(config_entry: ConfigEntry) -> DockerAppsOptionsFlow:
        return DockerAppsOptionsFlow()

    @classmethod
    @callback
    def async_get_supported_subentry_types(
        cls, config_entry: ConfigEntry
    ) -> dict[str, type[ConfigSubentryFlow]]:
        return {SUBENTRY_REPOSITORY: RepositorySubentryFlow}

    async def async_step_user(self, user_input: dict | None = None) -> ConfigFlowResult:
        errors = {}
        if user_input is not None:
            client = DockerClient(user_input[CONF_DOCKER_URL])
            try:
                await client.async_connect()
                containers = await client.async_containers(all=True)
            except DockerError:
                errors["base"] = "cannot_connect"
            else:
                self._data = {CONF_DOCKER_URL: user_input[CONF_DOCKER_URL]}
                self._suggested = suggest_folders(containers)
                return await self.async_step_folders()
            finally:
                await client.async_close()

        return self.async_show_form(
            step_id="user",
            data_schema=vol.Schema(
                {vol.Required(CONF_DOCKER_URL, default=DEFAULT_DOCKER_URL): str}
            ),
            errors=errors,
        )

    async def async_step_folders(
        self, user_input: dict | None = None
    ) -> ConfigFlowResult:
        if user_input is not None:
            return self.async_create_entry(
                title=DEFAULT_NAME,
                data={**self._data, **user_input},
            )
        apps_root, data_root = self._suggested
        return self.async_show_form(
            step_id="folders",
            data_schema=vol.Schema(
                {
                    vol.Required(CONF_APPS_ROOT, default=apps_root): str,
                    vol.Required(CONF_DATA_ROOT, default=data_root): str,
                    vol.Required(CONF_READ_ONLY, default=False): bool,
                }
            ),
        )


class DockerAppsOptionsFlow(OptionsFlow):
    """Options; the entry update listener reloads when they change."""

    async def async_step_init(self, user_input: dict | None = None) -> ConfigFlowResult:
        entry = self.config_entry
        current = {**entry.data, **entry.options}
        if user_input is not None:
            user_input[CONF_UNTRACKED] = [
                part.strip().lower()
                for part in user_input.get(CONF_UNTRACKED, "").split(",")
                if part.strip()
            ]
            return self.async_create_entry(data=user_input)
        untracked = current.get(CONF_UNTRACKED)
        if untracked is None:
            untracked = list(DEFAULT_UNTRACKED)
        return self.async_show_form(
            step_id="init",
            data_schema=vol.Schema(
                {
                    vol.Optional(CONF_UNTRACKED, default=", ".join(untracked)): str,
                    vol.Required(
                        CONF_APPS_ROOT,
                        default=current.get(CONF_APPS_ROOT, DEFAULT_APPS_ROOT),
                    ): str,
                    vol.Required(
                        CONF_DATA_ROOT,
                        default=current.get(CONF_DATA_ROOT, DEFAULT_DATA_ROOT),
                    ): str,
                    vol.Required(
                        CONF_READ_ONLY, default=current.get(CONF_READ_ONLY, False)
                    ): bool,
                    vol.Required(
                        CONF_UPDATE_HOURS,
                        default=current.get(CONF_UPDATE_HOURS, DEFAULT_UPDATE_HOURS),
                    ): NumberSelector(
                        NumberSelectorConfig(
                            min=1,
                            max=168,
                            mode=NumberSelectorMode.BOX,
                            unit_of_measurement="h",
                        )
                    ),
                }
            ),
        )


class RepositorySubentryFlow(ConfigSubentryFlow):
    """Add an app store repository."""

    _error = ""

    async def async_step_user(
        self, user_input: dict | None = None
    ) -> SubentryFlowResult:
        errors = {}
        if user_input is not None:
            url = user_input[CONF_REPO_URL].strip().rstrip("/")
            kind = user_input.get(CONF_REPO_KIND) or detect_kind(url)
            entry = self._get_entry()
            if any(s.data.get(CONF_REPO_URL) == url for s in entry.subentries.values()):
                return self.async_abort(reason="already_configured")
            if kind not in KINDS:
                errors[CONF_REPO_KIND] = "unknown_kind"
            else:
                if url in KNOWN_REPOSITORIES:
                    name = KNOWN_REPOSITORIES[url][1]
                else:
                    name = url.rsplit("/", 1)[-1]
                repository = Repository(url=url, kind=kind, name=name)
                try:
                    apps, _ = await async_fetch_repository(
                        async_get_clientsession(self.hass), self.hass, repository
                    )
                except (CatalogError, OSError, TimeoutError, ValueError) as err:
                    errors["base"] = "cannot_fetch"
                    self._error = str(err)
                else:
                    if not apps:
                        errors["base"] = "no_apps"
                    else:
                        return self.async_create_entry(
                            title=f"{name} ({len(apps)} apps)",
                            data={
                                CONF_REPO_URL: url,
                                CONF_REPO_KIND: kind,
                                "name": name,
                            },
                            unique_id=url,
                        )

        added = {
            s.data.get(CONF_REPO_URL) for s in self._get_entry().subentries.values()
        }
        options = [
            SelectOptionDict(value=url, label=f"{label} – {url}")
            for url, (_, label) in KNOWN_REPOSITORIES.items()
            if url not in added
        ]
        return self.async_show_form(
            step_id="user",
            data_schema=vol.Schema(
                {
                    vol.Required(CONF_REPO_URL): SelectSelector(
                        SelectSelectorConfig(
                            options=options,
                            custom_value=True,
                            mode=SelectSelectorMode.DROPDOWN,
                        )
                    ),
                    vol.Optional(CONF_REPO_KIND): SelectSelector(
                        SelectSelectorConfig(
                            options=list(KINDS), translation_key="repository_kind"
                        )
                    ),
                }
            ),
            errors=errors,
            description_placeholders={"error": self._error},
        )
