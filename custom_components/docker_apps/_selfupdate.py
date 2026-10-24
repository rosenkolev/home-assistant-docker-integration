"""Update the Home Assistant container itself.

A container can't recreate itself: stopping it ends the process doing the work. So
Home Assistant starts a one-shot container from the *new* Home Assistant image (no
extra image to download), with the same mounts, that runs this module:

    python3 -m custom_components.docker_apps._selfupdate

It waits a few seconds, recreates the Home Assistant service with the compose
engine and removes itself (AutoRemove).
"""

from __future__ import annotations

import asyncio
import json
import os
import sys
import typing
from datetime import UTC, datetime

from ._compose import (
    LABEL_CONFIG_HASH,
    ComposeEngine,
    ComposeError,
    build_container_config,
    load_model,
)
from ._docker_client import DockerClient
from ._models import LABEL_HELPER
from ._paths import PathMap, read_text
from .const import _LOGGER

if typing.TYPE_CHECKING:
    from ._compose import ComposeModel
    from .apps import AppManager

JOB_ENV = "DOCKER_APPS_JOB"
START_DELAY = 5


async def async_start_self_update(
    manager: AppManager, model: ComposeModel, service: str
) -> bool:
    """Start the one-shot updater when the Home Assistant container must change."""
    inspect = manager.self_inspect
    if inspect is None:
        raise ComposeError("the Home Assistant container can't be inspected")

    config = build_container_config(model, service)
    image_id = await manager.engine.async_image_id(config["Image"])
    labels = (inspect.get("Config") or {}).get("Labels") or {}
    if (
        labels.get(LABEL_CONFIG_HASH) == config["Labels"][LABEL_CONFIG_HASH]
        and inspect.get("Image") == image_id
    ):
        return False

    host = inspect.get("HostConfig") or {}
    job = {
        "project": model.project,
        "working_dir": model.working_dir,
        "files": model.config_files,
        "env_file": model.env_file,
        "service": service,
        "docker_url": manager.client.url,
        "path_map": manager.paths.as_list(),
    }
    body = {
        "Image": config["Image"],
        "Entrypoint": ["python3", "-m", "custom_components.docker_apps._selfupdate"],
        "WorkingDir": "/config",
        "Env": [f"{JOB_ENV}={json.dumps(job)}"],
        "Labels": {LABEL_HELPER: "1"},
        "HostConfig": {
            "Binds": host.get("Binds") or [],
            "Mounts": host.get("Mounts") or [],
            "NetworkMode": host.get("NetworkMode") or "bridge",
            "AutoRemove": True,
        },
    }
    name = f"docker-apps-self-update-{datetime.now(UTC).strftime('%Y%m%d%H%M%S')}"
    container_id = await manager.client.async_container_create(name, body)
    await manager.client.async_container_action(container_id, "start")
    _LOGGER.warning("Home Assistant will restart now to finish the update (%s)", name)
    return True


async def _async_run(job: dict) -> None:
    await asyncio.sleep(START_DELAY)  # let Home Assistant answer the update call
    paths = PathMap([tuple(m) for m in job["path_map"]] if job["path_map"] else None)

    def read(host_path: str) -> str | None:
        local = paths.to_local(host_path)
        return read_text(local) if local else None

    files = {f: read(f) for f in job["files"]}
    missing = [f for f, text in files.items() if text is None]
    if missing:
        raise RuntimeError(f"compose files not found: {missing}")
    env_text = read(job["env_file"]) if job.get("env_file") else None
    model = load_model(
        job["project"], job["working_dir"], files, env_text, job.get("env_file"), read
    )

    client = DockerClient(job["docker_url"])
    try:
        await client.async_connect()
        await ComposeEngine(client).async_up(
            model, services=[job["service"]], remove_orphans=False
        )
    finally:
        await client.async_close()


def main() -> int:
    job = json.loads(os.environ[JOB_ENV])
    try:
        asyncio.run(_async_run(job))
    except Exception as err:  # noqa: BLE001
        print(f"Self-update failed: {err}", file=sys.stderr)
        return 1
    print("Self-update done")
    return 0


if __name__ == "__main__":
    sys.exit(main())
