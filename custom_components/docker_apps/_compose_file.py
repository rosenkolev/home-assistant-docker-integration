"""Edit image tags in compose files without reformatting them.

Hand-written compose files keep their comments and layout: only the `image:` value
(or the `.env` variable it uses, as in `immich:${IMMICH_VERSION:-release}`) changes.
"""

# cspell:words keepends

from __future__ import annotations

import re
from dataclasses import dataclass

from ._compose import untag, load_yaml

# one variable in a tag, with optional fixed text around it: `${VERSION:-release}-rknn`
_VAR = re.compile(
    r"^(?P<pre>[^$]*)\$(?:\{(?P<braced>[A-Za-z_][A-Za-z0-9_]*)(?::?-[^}]*)?\}|(?P<plain>[A-Za-z_][A-Za-z0-9_]*))(?P<post>[^$]*)$"
)


@dataclass
class ImageEdit:
    """Result of an image tag change for one service."""

    file: str
    content: str
    env_content: str | None = None
    env_var: tuple[str, str] | None = None  # (name, new value)


def parse_services(text: str) -> dict[str, dict]:
    data = untag(load_yaml(text) or {})
    services = data.get("services") or {}
    return {k: v or {} for k, v in services.items() if isinstance(v, dict)}


def find_image(files: dict[str, str], service: str) -> tuple[str, str] | None:
    """Return (file, raw image value) of the file that defines the service image.

    Later files override earlier ones, like `docker compose -f a -f b`.
    """
    for name in reversed(list(files)):
        image = parse_services(files[name]).get(service, {}).get("image")
        if image:
            return name, str(image)
    return None


def split_raw_image(raw: str) -> tuple[str, str | None]:
    """Split a raw compose image into (repository, tag expression)."""
    raw = raw.split("@", 1)[0]
    head, _, last = raw.rpartition("/")
    if ":" in last:
        name, tag = last.split(":", 1)
        return (f"{head}/{name}" if head else name), tag
    return raw, None


def replace_image_line(text: str, service: str, old: str, new: str) -> str:
    """Replace the image value of one service, keeping quotes and comments."""
    lines = text.splitlines(keepends=True)
    in_services = False
    service_indent: int | None = None
    in_service = False

    for index, line in enumerate(lines):
        stripped = line.strip()
        if not stripped or stripped.startswith("#"):
            continue
        indent = len(line) - len(line.lstrip(" "))
        key = stripped.split(":", 1)[0].strip("'\"")

        if indent == 0:
            in_services = key == "services"
            service_indent = None
            in_service = False
            continue
        if not in_services:
            continue

        if service_indent is None:
            service_indent = indent
        if indent == service_indent:
            in_service = key == service
            continue

        if in_service and key == "image":
            pattern = re.compile(
                r"^(?P<head>\s*image:\s*[\"']?)"
                + re.escape(old)
                + r"(?P<tail>[\"']?.*)$",
                re.DOTALL,
            )
            match = pattern.match(line)
            if match:
                lines[index] = f"{match['head']}{new}{match['tail']}"
                return "".join(lines)

    raise ValueError(f"image '{old}' of service '{service}' not found")


def set_env_value(text: str, name: str, value: str) -> str:
    """Set NAME=value in a .env file, keeping the other lines."""
    lines = text.splitlines(keepends=True)
    pattern = re.compile(rf"^(\s*(?:export\s+)?{re.escape(name)}\s*=\s*)(.*?)(\s*)$")
    for index, line in enumerate(lines):
        match = pattern.match(line)
        if match:
            quote = match[2][:1] if match[2][:1] in "'\"" else ""
            lines[index] = f"{match[1]}{quote}{value}{quote}\n"
            return "".join(lines)
    if lines and not lines[-1].endswith("\n"):
        lines[-1] += "\n"
    lines.append(f"{name}={value}\n")
    return "".join(lines)


def update_service_tag(
    files: dict[str, str],
    env_file: str | None,
    env_text: str | None,
    service: str,
    new_tag: str,
) -> ImageEdit:
    """Change the tag of a service image in its compose file or in the .env file."""
    found = find_image(files, service)
    if found is None:
        raise ValueError(f"service '{service}' has no image in the compose files")
    file, raw = found
    repository, tag = split_raw_image(raw)

    if tag and (var := _VAR.match(tag)):
        name = var["braced"] or var["plain"]
        if env_file is None:
            raise ValueError(f"'{raw}' uses ${name} but there is no .env file")
        pre, post = var["pre"], var["post"]
        if not (new_tag.startswith(pre) and new_tag.endswith(post)) or len(
            new_tag
        ) <= len(pre) + len(post):
            raise ValueError(f"tag {new_tag} doesn't fit '{tag}'")
        value = new_tag[len(pre) : len(new_tag) - len(post)]
        return ImageEdit(
            file=file,
            content=files[file],
            env_content=set_env_value(env_text or "", name, value),
            env_var=(name, value),
        )

    if "$" in repository or (tag and "$" in tag):
        raise ValueError(f"'{raw}' uses variables that can't be updated")

    return ImageEdit(
        file=file,
        content=replace_image_line(
            files[file], service, raw, f"{repository}:{new_tag}"
        ),
    )
