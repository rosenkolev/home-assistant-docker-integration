"""What a store app may do on the host, beyond its own folders and ports.

Shown before an install, so the user decides; an update that asks for more is held
until the user allows it.
"""

# cspell:words seccomp userns

from __future__ import annotations

import posixpath

from ._compose import is_bind_source, split_volume

# host files an app may read without it being access worth a question
HARMLESS = ("/etc/localtime", "/etc/timezone", "/usr/share/zoneinfo")
UNCONFINED = (
    "apparmor=unconfined",
    "apparmor:unconfined",
    "seccomp=unconfined",
    "seccomp:unconfined",
    "systempaths=unconfined",
)


def _inside(path: str, folder: str) -> bool:
    return path == folder or path.startswith(folder.rstrip("/") + "/")


def _list(value) -> list:
    if value is None:
        return []
    return value if isinstance(value, list) else [value]


def host_access(compose: dict, app_dir: str, data_root: str) -> list[str]:
    """The host access a compose file asks for, in words, sorted (for comparing)."""
    found: set[str] = set()
    for service in (compose.get("services") or {}).values():
        if not isinstance(service, dict):
            continue
        if service.get("privileged"):
            found.add("Privileged mode (all devices, can leave the container)")
        for key, words in (
            ("network_mode", "Host network"),
            ("pid", "Host processes"),
            ("ipc", "Host shared memory"),
            ("uts", "Host name"),
            ("userns_mode", "Host user namespace"),
            ("cgroup", "Host control groups"),
        ):
            if service.get(key) == "host":
                found.add(words)
        for cap in _list(service.get("cap_add")):
            found.add(f"Capability {str(cap).upper().removeprefix('CAP_')}")
        for option in _list(service.get("security_opt")):
            if str(option).replace(" ", "") in UNCONFINED:
                found.add(f"Security profile off ({option})")
        for device in _list(service.get("devices")):
            source = device.get("source") if isinstance(device, dict) else device
            found.add(f"Device {str(source).split(':')[0]}")
        if service.get("device_cgroup_rules"):
            found.add("Device rules (device_cgroup_rules)")
        reservations = ((service.get("deploy") or {}).get("resources") or {}).get(
            "reservations"
        ) or {}
        if service.get("gpus") or reservations.get("devices"):
            found.add("GPU")
        for volume in _list(service.get("volumes")):
            if isinstance(volume, dict):
                if volume.get("type", "volume") != "bind":
                    continue
                source = str(volume.get("source") or "")
            else:
                source = split_volume(volume)[0] or ""
            if not source or not is_bind_source(source):
                continue
            path = posixpath.normpath(posixpath.join(app_dir, source))
            if path.endswith("docker.sock"):
                found.add(f"Full control of Docker ({path})")
            elif path == posixpath.normpath(data_root):
                found.add(f"The data of all apps ({path})")
            elif not (
                _inside(path, app_dir)
                or _inside(path, data_root)
                or any(_inside(path, h) for h in HARMLESS)
            ):
                found.add(f"Host folder {path}")
    return sorted(found)


def check_store_files(compose: dict, app_dir: str) -> None:
    """A store app's env_file and extends stay in its own folder: one outside could
    hand it another app's passwords."""
    for name, service in (compose.get("services") or {}).items():
        if not isinstance(service, dict):
            continue
        files = [
            entry.get("path") if isinstance(entry, dict) else entry
            for entry in _list(service.get("env_file"))
        ]
        extends = service.get("extends")
        if isinstance(extends, dict) and extends.get("file"):
            files.append(extends["file"])
        for file in files:
            path = posixpath.normpath(posixpath.join(app_dir, str(file or "")))
            if not _inside(path, app_dir):
                raise ValueError(
                    f"service '{name}' reads {file}, outside the app folder"
                )
