"""Map host paths to paths inside the Home Assistant container.

Compose labels hold host paths (`/srv/apps/immich`). Home Assistant reads
and writes those files through its own bind mounts, found by inspecting its own
container. Outside a container, paths are used as they are.
"""

# cspell:words CREAT RDONLY WRONLY

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


# files that hold secrets: passwords in .env, the user's own values in the override
PRIVATE_FILES = (".env", "compose.override.yaml")

Owner = tuple[int, int]  # (uid, gid) of the user the app files belong to


def is_private(path: str) -> bool:
    return path.endswith(PRIVATE_FILES)


def set_owner(path: str, owner: Owner | None) -> bool:
    """Give a file or folder to the owner; False where that can't be done (not
    root, or Windows)."""
    if owner is None or not hasattr(os, "chown"):
        return False
    try:
        os.chown(path, *owner)
    except OSError:
        return False
    return True


def make_folders(folder: str, owner: Owner | None = None) -> None:
    """Create a folder and its missing parents; the new ones belong to the owner
    and are closed to other users (0770)."""
    missing = []
    while folder and not os.path.isdir(folder):
        missing.append(folder)
        parent = os.path.dirname(folder)
        if parent == folder:
            break
        folder = parent
    for path in reversed(missing):
        os.makedirs(path, exist_ok=True)
        if set_owner(path, owner):
            os.chmod(path, 0o770)


def write_text(
    path: str, content: str, private: bool = False, owner: Owner | None = None
) -> None:
    """Replace a file atomically; on disk before it counts, so a power cut leaves
    the old content or the new one, never an empty file.

    A private file is readable by its owner only (0600), or with `owner` by that
    user and group (0660), so tools running as the user can open it. It is never
    readable by others, not even while it is written.
    """
    folder = os.path.dirname(path)
    make_folders(folder, owner)
    tmp = f"{path}.docker-apps.tmp"
    fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with open(fd, "w", encoding="utf-8", newline="\n") as file:
        file.write(content)
        file.flush()
        os.fsync(file.fileno())
    owned = set_owner(tmp, owner)
    if private:
        os.chmod(tmp, 0o660 if owned else 0o600)
    else:
        os.chmod(tmp, 0o664 if owned else 0o644)
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


def copy_file(source: str, target: str, owner: Owner | None = None) -> bool:
    content = read_text(source)
    if content is None:
        return False
    write_text(target, content, private=is_private(source), owner=owner)
    return True


def remove_file(path: str) -> None:
    with contextlib.suppress(FileNotFoundError):
        os.remove(path)


def remove_tree(path: str) -> None:
    shutil.rmtree(path, ignore_errors=True)
