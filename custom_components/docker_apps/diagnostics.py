"""Diagnostics download: host, apps, settings (secrets redacted) and repositories."""

from __future__ import annotations

from dataclasses import asdict
from typing import Any

from homeassistant.components.diagnostics import async_redact_data
from homeassistant.core import HomeAssistant

from .coordinator import DockerAppsConfigEntry

TO_REDACT = {"values"}


async def async_get_config_entry_diagnostics(
    hass: HomeAssistant, entry: DockerAppsConfigEntry
) -> dict[str, Any]:
    runtime = entry.runtime_data
    data = runtime.docker.data
    updates = runtime.updates.data or {}
    return {
        "options": {**entry.data, **entry.options},
        "host": asdict(data.host),
        "self_container": runtime.manager.self_container_id,
        "mounts": runtime.manager.paths.as_list(),
        "apps": {
            key: {
                "project": app.project,
                "working_dir": app.working_dir,
                "config_files": app.config_files,
                "editable": runtime.manager.can_edit(app),
                "containers": [
                    {
                        "key": c.key,
                        "image": c.image,
                        "state": c.state,
                        "health": c.health,
                    }
                    for c in data.app_containers(key)
                ],
                "update": (
                    {
                        "installed": u.installed_version,
                        "latest": u.latest_version,
                        "blocked": u.blocked,
                        "error": u.error,
                        "services": [asdict(s) for s in u.services],
                    }
                    if (u := updates.get(key))
                    else None
                ),
            }
            for key, app in data.apps.items()
        },
        # redacts the store values of each app and of its history entries
        "settings": async_redact_data(
            {k: asdict(v) for k, v in runtime.manager.settings.items()}, TO_REDACT
        ),
        "repositories": [
            {"url": r.url, "kind": r.kind, "error": runtime.catalog.errors.get(r.id)}
            for r in runtime.catalog.repositories.values()
        ],
        "store_apps": len(runtime.catalog.apps),
    }
