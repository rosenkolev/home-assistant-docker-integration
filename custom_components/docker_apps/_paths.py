"""Map host paths to paths inside the Home Assistant container.

Compose labels hold host paths (`/srv/apps/immich`). Home Assistant reads
and writes those files through its own bind mounts, found by inspecting its own
container. Outside a container, paths are used as they are.
"""

# cspell:words RDONLY

from __future__ import annotations

import contextlib
import os
import shutil
from pathlib import PurePosixPath


class PathMap:
    def __init__(self, mounts: list[tuple[str, str]] | None):
        # (host source, container destination), longest source first
        self.mounts = sorted(mounts or [], key=lambda m: len(m[0]), reverse=True)
        self.in_container = mounts is not None

    @classmethod
    def from_inspect(cls, inspect: dict | None) -> PathMap:
        if inspect is None:
            return cls(None)
        return cls(
            [
                (m["Source"].rstrip("/") or "/", m["Destination"].rstrip("/") or "/")
                for m in inspect.get("Mounts") or []
                if m.get("Type") == "bind" and m.get("Source") and m.get("Destination")
            ]
        )

    def to_local(self, host_path: str) -> str | None:
        """Return the local path of a host path, or None when not mounted."""
        if not self.in_container:
            return host_path
        path = PurePosixPath(host_path)
        for source, destination in self.mounts:
            try:
                rest = path.relative_to(source)
            except ValueError:
                continue
            return str(PurePosixPath(destination) / rest)
        return None

    def as_list(self) -> list[list[str]] | None:
        return [list(m) for m in self.mounts] if self.in_container else None


def read_text(path: str) -> str | None:
    try:
        with open(path, encoding="utf-8") as file:
            return file.read()
    except FileNotFoundError:
        return None


def write_text(path: str, content: str, private: bool = False) -> None:
    """Replace a file atomically; on disk before it counts, so a power cut leaves
    the old content or the new one, never an empty file."""
    folder = os.path.dirname(path)
    os.makedirs(folder, exist_ok=True)
    tmp = f"{path}.docker-apps.tmp"
    with open(tmp, "w", encoding="utf-8", newline="\n") as file:
        file.write(content)
        file.flush()
        os.fsync(file.fileno())
    if private:
        os.chmod(tmp, 0o600)
    os.replace(tmp, path)
    sync_folder(folder)


def sync_folder(folder: str) -> None:
    """Put a rename in the folder on disk (Linux; Windows can't open folders)."""
    if os.name == "nt":
        return
    fd = os.open(folder or ".", os.O_RDONLY)
    try:
        os.fsync(fd)
    finally:
        os.close(fd)


def copy_file(source: str, target: str) -> bool:
    content = read_text(source)
    if content is None:
        return False
    write_text(target, content, private=source.endswith(".env"))
    return True


def remove_file(path: str) -> None:
    with contextlib.suppress(FileNotFoundError):
        os.remove(path)


def remove_tree(path: str) -> None:
    shutil.rmtree(path, ignore_errors=True)
