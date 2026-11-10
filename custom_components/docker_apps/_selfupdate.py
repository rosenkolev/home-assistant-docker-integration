"""Update the Home Assistant container itself.

A container can't recreate itself: stopping it ends the process doing the work. So
Home Assistant starts a one-shot container from the *new* Home Assistant image (no
extra image to download), with the same mounts, that runs this module:

    python3 -m custom_components.docker_apps._selfupdate

It waits a few seconds and recreates the Home Assistant service with the compose
engine. The old container is stopped and kept until the new one stays up; when the
new one doesn't, the old one is started again and the backup of the compose files
put back (as Supervisor does on Home Assistant OS). The outcome goes to RESULT_FILE
for Home Assistant to show, and the helper removes itself (AutoRemove).
"""

from __future__ import annotations

import asyncio
import json
import os
import sys
import typing
from datetime import UTC, datetime
from pathlib import PurePosixPath

from ._compose import (
    LABEL_CONFIG_HASH,
    ComposeEngine,
    ComposeError,
    build_container_config,
    container_name,
    load_model,
)
from ._docker_client import DockerClient, DockerError
from ._models import LABEL_HELPER, NEW_SUFFIX
from ._paths import PathMap, copy_file, read_text, write_text
from .const import _LOGGER

if typing.TYPE_CHECKING:
    from ._compose import ComposeModel
    from .apps import AppManager

JOB_ENV = "DOCKER_APPS_JOB"
START_DELAY = 5
# where the helper leaves the outcome, relative to the Home Assistant config folder
RESULT_FILE = ".storage/docker_apps.self_update.json"
# the new container counts as up once it runs this long without a restart
UP_STABLE = 60
UP_TIMEOUT = 300
POLL = 5


async def async_start_self_update(
    manager: AppManager,
    app_key: str,
    model: ComposeModel,
    service: str,
    backup: dict | None = None,
) -> bool:
    """Start the one-shot updater when the Home Assistant container must change.

    `backup` (a history entry) is put back when the new container doesn't come up.
    """
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
        "app": app_key,
        "project": model.project,
        "working_dir": model.working_dir,
        "files": model.config_files,
        "env_file": model.env_file,
        "service": service,
        "docker_url": manager.client.url,
        "path_map": manager.paths.as_list(),
        "backup": {k: backup[k] for k in ("dir", "files", "version")}
        if backup
        else None,
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


async def async_wait_up(client: DockerClient, name: str) -> str | None:
    """None once the container runs UP_STABLE seconds without a restart, else why
    not (after UP_TIMEOUT, or at once when it stopped and won't restart)."""
    loop = asyncio.get_running_loop()
    deadline = loop.time() + UP_TIMEOUT
    since: tuple[str, float] | None = None  # (StartedAt, first seen running)
    while True:
        info = await client.async_container_inspect(name)
        state = info.get("State") or {}
        if state.get("Running") and not state.get("Restarting"):
            if since is None or since[0] != state.get("StartedAt"):
                since = (state.get("StartedAt"), loop.time())
            if loop.time() - since[1] >= UP_STABLE:
                return None
        else:
            since = None
        policy = ((info.get("HostConfig") or {}).get("RestartPolicy") or {}).get("Name")
        why = (
            f"state {state.get('Status', 'unknown')}, exit code "
            f"{state.get('ExitCode')}, {info.get('RestartCount', 0)} restarts"
        )
        if state.get("Status") in ("exited", "dead") and policy in (None, "", "no"):
            return why
        if loop.time() > deadline:
            return f"it did not stay up for {UP_STABLE} seconds: {why}"
        await asyncio.sleep(POLL)


def restore_backup(backup: dict | None, paths: PathMap) -> bool:
    """Put back the compose files of the backup; False when there is none."""
    if not backup:
        return False
    for path in backup["files"]:
        source = paths.to_local(f"{backup['dir']}/{PurePosixPath(path).name}")
        target = paths.to_local(path)
        if source and target:
            copy_file(source, target)
    return True


async def _async_run(job: dict) -> dict:
    """Update and watch the Home Assistant container; returns the outcome."""
    await asyncio.sleep(START_DELAY)  # let Home Assistant answer the update call
    paths = PathMap([tuple(m) for m in job["path_map"]] if job["path_map"] else None)
    result: dict = {
        "app": job["app"],
        "time": datetime.now(UTC).isoformat(),
        "backup": job.get("backup"),
    }

    def read(host_path: str) -> str | None:
        local = paths.to_local(host_path)
        return read_text(local) if local else None

    client = DockerClient(job["docker_url"])
    engine = ComposeEngine(client)
    kept: dict[str, str] = {}
    name = ""
    try:
        try:
            files = {f: read(f) for f in job["files"]}
            missing = [f for f, text in files.items() if text is None]
            if missing:
                raise ComposeError(f"compose files not found: {missing}")
            env_text = read(job["env_file"]) if job.get("env_file") else None
            model = load_model(
                job["project"],
                job["working_dir"],
                files,
                env_text,
                job.get("env_file"),
                read,
            )
            name = container_name(model, job["service"])
            await client.async_connect()
            await engine.async_up(
                model, services=[job["service"]], remove_orphans=False, kept=kept
            )
            error = await async_wait_up(client, name)
        except Exception as err:  # noqa: BLE001
            error = str(err) or type(err).__name__
        if error is None:
            for old in kept.values():
                await engine.async_remove(old)
            return result | {"status": "updated"}

        result |= {"status": "failed", "error": error}  # the old one never stopped
        if old := kept.get(job["service"]):
            try:
                await _async_put_back(client, engine, old, name)
                result["status"] = "rolled_back"
            except Exception as undo:  # noqa: BLE001
                result["status"] = "broken"
                result["error"] += (
                    f"; starting the previous container failed too: {undo}"
                )
        try:
            result["files_restored"] = restore_backup(job.get("backup"), paths)
        except OSError as err:
            result["error"] += f"; the compose files were not put back: {err}"
        return result
    finally:
        await client.async_close()


async def _async_put_back(
    client: DockerClient, engine: ComposeEngine, old: str, name: str
) -> None:
    """Remove the new container (by its name, or the temporary one) and start the
    old one again under its name."""
    for new in (name, f"{name}{NEW_SUFFIX}"):
        try:
            await engine.async_remove(new)
        except DockerError as err:
            if err.status != 404:
                raise
    await client.async_container_rename(old, name)
    await client.async_container_action(old, "start")


def main() -> int:
    job = json.loads(os.environ[JOB_ENV])
    result = asyncio.run(_async_run(job))
    print(f"Self-update: {json.dumps(result)}")
    try:
        write_text(RESULT_FILE, json.dumps(result))
    except OSError as err:
        print(f"Self-update result not written: {err}", file=sys.stderr)
    return 0 if result["status"] == "updated" else 1


if __name__ == "__main__":
    sys.exit(main())
