"""App store repositories: CasaOS and Runtipi, read from repository archives.

Every source is turned into the same normalized `StoreApp`. Rendering an app gives a
plain compose file plus a `.env`, so installed apps are ordinary compose projects.
"""

# cspell:words fqdnip

# cspell:words armhf armv Baïkal dbgate dotenv Selkies sysctls ulimits

from __future__ import annotations

import hashlib
import io
import json
import re
import secrets
import string
import tarfile
import unicodedata
import zipfile
from collections.abc import Callable
from dataclasses import asdict, dataclass, field

import logging

import yaml

from ._models import split_image
from ._yaml import safe_load

_LOGGER = logging.getLogger(__name__)
KIND_CASAOS = "casaos"
KIND_RUNTIPI = "runtipi"
KINDS = (KIND_CASAOS, KIND_RUNTIPI)

ARCH_ALIASES = {"x86_64": "amd64", "aarch64": "arm64", "armv7l": "arm", "armhf": "arm"}
_GITHUB = re.compile(r"^https://github\.com/([^/]+)/([^/#?]+?)(?:\.git)?/?$")
_SAFE_ID = re.compile(r"[^a-z0-9_-]+")
_TEMPLATE_VAR = re.compile(r"\{\{\s*(\w+)\s*\}\}")
# linuxserver.io desktop images (Selkies) serve HTTP on 3000 and HTTPS on 3001; the
# desktop stream only works over HTTPS
LINUXSERVER_IMAGES = ("linuxserver/", "lscr.io/linuxserver/", "ghcr.io/linuxserver/")


@dataclass
class StoreVariable:
    key: str
    label: str
    type: str = "text"  # Runtipi field type: random is generated, others are asked
    default: str | None = None
    required: bool = False
    min: int | None = None


@dataclass
class StoreApp:
    id: str
    repo_id: str
    app_id: str
    kind: str
    name: str
    version: str
    package_version: str
    tagline: str = ""
    description: str = ""
    icon: str | None = None
    categories: list[str] = field(default_factory=list)
    architectures: list[str] = field(default_factory=list)
    website: str | None = None
    source: str | None = None
    release_notes: str | None = None
    content_hash: str | None = None
    main_service: str | None = None
    web_port: int | None = None
    web_path: str = "/"
    web_target: int | None = None  # container port of the web UI
    web_scheme: str = "http"
    variables: list[StoreVariable] = field(default_factory=list)
    compose: str | None = None
    # the Runtipi data folder (path in it: text), copied into APP_DATA_DIR/data
    files: dict[str, str] = field(default_factory=dict)

    def as_dict(self, with_compose: bool = True) -> dict:
        data = asdict(self)
        if not with_compose:
            data.pop("compose")
            data.pop("files")
        return data

    @classmethod
    def from_dict(cls, data: dict) -> StoreApp:
        data = dict(data)
        data["variables"] = [StoreVariable(**v) for v in data.get("variables") or []]
        return cls(**data)

    def supports(self, arch: str) -> bool:
        if not self.architectures:
            return True
        arch = ARCH_ALIASES.get(arch, arch)
        return arch in {ARCH_ALIASES.get(a, a) for a in self.architectures}

    def is_newer_than(
        self, package_version: str | None, content_hash: str | None
    ) -> bool:
        if package_version is None:
            return False
        if self.package_version.isdigit() and package_version.isdigit():
            return int(self.package_version) > int(package_version)
        if self.content_hash and content_hash:
            return self.content_hash != content_hash
        return self.package_version != package_version


def safe_id(value: str) -> str:
    # accents become plain letters: Baïkal is baikal, not ba-kal
    plain = unicodedata.normalize("NFKD", value).encode("ascii", "ignore").decode()
    return _SAFE_ID.sub("-", plain.lower()).strip("-") or "app"


def repo_id_for(url: str) -> str:
    match = _GITHUB.match(url)
    base = f"{match[1]}-{match[2]}" if match else url
    return safe_id(base)[:40] + "-" + hashlib.sha1(url.encode()).hexdigest()[:6]


def raw_file_url(url: str) -> str | None:
    """Base URL of single files in a GitHub repository, or None."""
    if match := _GITHUB.match(url):
        return f"https://raw.githubusercontent.com/{match[1]}/{match[2]}/HEAD"
    return None


def archive_url(url: str) -> str:
    """Turn a GitHub repository URL into its tarball URL; other URLs stay."""
    if match := _GITHUB.match(url):
        return f"https://codeload.github.com/{match[1]}/{match[2]}/tar.gz/HEAD"
    return url


def archive_urls(url: str, kind: str) -> list[str]:
    """The downloads to try, in order.

    A CasaOS store on GitHub publishes its apps as a small main.zip release, the
    file CasaOS itself reads; the whole repository can be hundreds of MB of images.
    """
    match = _GITHUB.match(url)
    if match and kind == KIND_CASAOS:
        release = f"https://github.com/{match[1]}/{match[2]}/releases/latest/download/main.zip"
        return [release, archive_url(url)]
    return [archive_url(url)]


def _localized(value, locale: str = "en_US") -> str:
    if isinstance(value, dict):
        value = (
            value.get(locale)
            or value.get("en_US")
            or value.get("en_GB")
            or next(iter(value.values()), "")
        )
    return _text(value)


# --- store fields: cleaned to their type, a store can hold anything ---

_ENV_NAME = re.compile(r"[A-Za-z_][A-Za-z0-9_]*")
FIELD_TYPES = {
    "text", "password", "email", "number", "fqdn", "ip", "fqdnip", "url",
    "random", "boolean",
}  # fmt: skip


def _text(value) -> str:
    """Text, from a number too; anything else (a list, an object) is no text."""
    if isinstance(value, bool) or not isinstance(value, (str, int, float)):
        return ""
    return str(value)


def _texts(value) -> list[str]:
    """A list of texts; one text is a list of one."""
    items = value if isinstance(value, list) else [value]
    return [text for text in map(_text, items) if text]


def _link(value) -> str | None:
    """Only web links: `javascript:` and the like would run in Home Assistant."""
    text = _text(value).strip()
    return text if text.lower().startswith(("http://", "https://")) else None


def _web_path(value) -> str:
    """A path on the app's own address: `@evil.example/` would lead elsewhere."""
    text = _text(value).strip()
    return text if text.startswith("/") and not text.startswith("//") else "/"


def _port(value) -> int | None:
    text = _text(value).strip()
    return int(text) if text.isdigit() and 0 < int(text) < 65536 else None


def _int(value) -> int | None:
    text = _text(value).strip()
    return int(text) if text.lstrip("-").isdigit() else None


def _hash(*parts: str) -> str:
    digest = hashlib.sha256()
    for part in parts:
        digest.update(part.encode("utf-8"))
    return "sha256:" + digest.hexdigest()[:32]


def _port_pairs(service: dict) -> list[tuple[str, str]]:
    """(published, target) of each port of a compose service, as text."""
    pairs = []
    for port in service.get("ports") or []:
        if isinstance(port, dict):
            pairs.append((str(port.get("published") or ""), str(port.get("target"))))
        else:
            parts = str(port).split("/")[0].split(":")
            pairs.append((parts[-2] if len(parts) >= 2 else "", parts[-1]))
    return pairs


def web_endpoint(
    service: dict, published: str | None, scheme: str | None = None
) -> tuple[int | None, int | None, str]:
    """(host port, container port, scheme) of the web UI of a store app.

    `published` is the host port the store names for the web UI. The scheme comes
    from the store, or from the image when it is known to need HTTPS.
    """
    pairs = _port_pairs(service)
    target = next((t for p, t in pairs if published and p == published), None)
    image = str(service.get("image") or "")
    if target == "3000" and image.startswith(LINUXSERVER_IMAGES):
        https = next((p for p, t in pairs if t == "3001"), None)
        if https is not None:
            return (int(https) if https.isdigit() else None), 3001, "https"
    return (
        int(published) if published and published.isdigit() else None,
        int(target) if target and target.isdigit() else None,
        scheme if scheme in ("http", "https") else "http",
    )


# --- CasaOS ---


def parse_casaos_compose(text: str, repo_id: str) -> StoreApp | None:
    data = safe_load(text) or {}
    meta = data.get("x-casaos") if isinstance(data, dict) else None
    services = data.get("services") if isinstance(data, dict) else None
    if not isinstance(meta, dict) or not meta or not isinstance(services, dict):
        return None
    if not services:
        return None
    app_id = safe_id(
        _text(meta.get("id")) or _text(data.get("name")) or str(next(iter(services)))
    )
    port = _text(meta.get("port_map")).strip()
    main_name = _text(meta.get("main")) or None
    # some stores (BigBear) have no version: show the main image tag instead
    main = services.get(main_name) or next(iter(services.values()))
    main = main if isinstance(main, dict) else {}
    version = _text(meta.get("version")) or split_image(_text(main.get("image")))[1]
    web_port, web_target, web_scheme = web_endpoint(
        main, port, _text(meta.get("scheme")).lower()
    )
    return StoreApp(
        id=f"{repo_id}.{app_id}",
        repo_id=repo_id,
        app_id=app_id,
        kind=KIND_CASAOS,
        name=_localized(meta.get("title")) or app_id,
        version=version or "",
        package_version=_text(meta.get("version")) or _hash(text),
        tagline=_localized(meta.get("tagline")),
        description=_localized(meta.get("description")),
        icon=_link(meta.get("icon")),
        categories=_texts(meta.get("category")),
        architectures=_texts(meta.get("architectures")),
        website=_link(meta.get("website")),
        source=_link(meta.get("repo")),
        release_notes=_localized(meta.get("release_notes")) or None,
        content_hash=_hash(text),
        main_service=main_name,
        web_port=web_port,
        web_path=_web_path(meta.get("index")),
        web_target=web_target,
        web_scheme=web_scheme,
        compose=text,
    )


def render_casaos(app: StoreApp, project: str, data_dir: str, data_root: str) -> dict:
    data = safe_load(app.compose or "") or {}
    data.pop("x-casaos", None)
    data["name"] = project
    for service in (data.get("services") or {}).values():
        if isinstance(service, dict):
            service.pop("x-casaos", None)
    text = yaml.safe_dump(data, sort_keys=False, allow_unicode=True)
    for pattern in ("/DATA/AppData/$AppID", "/DATA/AppData/${AppID}"):
        text = text.replace(pattern, data_dir)
    text = text.replace("/DATA/AppData/", f"{data_root}/").replace(
        "/DATA/", f"{data_root}/"
    )
    return safe_load(text)


# --- Runtipi ---


def parse_runtipi(
    config_text: str,
    compose_text: str | None,
    dynamic_text: str | None,
    repo_id: str,
    description: str = "",
) -> StoreApp | None:
    config = json.loads(config_text)
    if not isinstance(config, dict):
        return None
    if not config.get("available", True) or config.get("deprecated"):
        return None
    app_id = safe_id(_text(config.get("id")))
    if not _text(config.get("id")):
        return None
    # Runtipi uses one file: docker-compose.yml in its new format (with x-runtipi),
    # else docker-compose.json. An old docker-compose.yml next to the json is kept
    # for old Runtipi versions only and is often out of date.
    yml = (safe_load(compose_text) if compose_text else None) or {}
    if dynamic_text and not (isinstance(yml, dict) and "x-runtipi" in yml):
        compose = yaml.safe_dump(
            runtipi_dynamic_to_compose(json.loads(dynamic_text)),
            sort_keys=False,
            allow_unicode=True,
        )
    else:
        compose = compose_text
    if compose is None:
        return None
    compose = _publish_main_port(compose)

    fields = config.get("form_fields")
    variables = [
        StoreVariable(
            key=field_["env_variable"],
            label=_text(field_.get("label")) or field_["env_variable"],
            type=(field_.get("type") if field_.get("type") in FIELD_TYPES else "text"),
            default=(
                str(field_["default"])
                if isinstance(field_.get("default"), (str, int, float))
                else None
            ),
            required=bool(field_.get("required")),
            min=_int(field_.get("min")),
        )
        for field_ in (fields if isinstance(fields, list) else [])
        # the name goes into .env: anything else could add lines to it
        if isinstance(field_, dict)
        and isinstance(field_.get("env_variable"), str)
        and _ENV_NAME.fullmatch(field_["env_variable"])
    ]

    services = (safe_load(compose) or {}).get("services") or {}
    main = next(
        (
            s
            for s in services.values()
            if isinstance(s, dict) and "${APP_PORT}" in str(s.get("ports"))
        ),
        {},
    )
    web_port, web_target, web_scheme = web_endpoint(main, "${APP_PORT}")
    return StoreApp(
        id=f"{repo_id}.{app_id}",
        repo_id=repo_id,
        app_id=app_id,
        kind=KIND_RUNTIPI,
        name=_text(config.get("name")) or app_id,
        version=_text(config.get("version")),
        package_version=_text(config.get("tipi_version")) or "0",
        tagline=_text(config.get("short_desc")),
        description=description or _text(config.get("description")),
        categories=_texts(config.get("categories")),
        architectures=_texts(config.get("supported_architectures")),
        website=_link(config.get("website")),
        source=_link(config.get("source")),
        content_hash=_hash(config_text, compose),
        web_port=_port(config.get("port")) if web_target != 3001 else web_port,
        web_target=web_target,
        web_scheme=web_scheme,
        variables=variables,
        compose=compose,
    )


def _publish_main_port(compose: str) -> str:
    """Schema v2 compose files mark the web UI with `x-runtipi: {is_main,
    internal_port}` instead of a port; Runtipi publishes it on APP_PORT."""
    data = safe_load(compose) or {}
    changed = False
    for service in (data.get("services") or {}).values():
        meta = (isinstance(service, dict) and service.get("x-runtipi")) or {}
        if not (meta.get("is_main") and meta.get("internal_port")):
            continue
        ports = service.get("ports") or []
        if "${APP_PORT}" not in str(ports):
            service["ports"] = [f"${{APP_PORT}}:{meta['internal_port']}", *ports]
            changed = True
    if not changed:
        return compose
    return yaml.safe_dump(data, sort_keys=False, allow_unicode=True)


# keys of a Runtipi docker-compose.json service that are compose keys as they are,
# or under another name
_DYNAMIC_SAME = (
    "image", "command", "entrypoint", "user", "hostname", "tty", "privileged",
    "devices", "pid", "ulimits", "sysctls", "logging", "dns", "restart",
)  # fmt: skip
_DYNAMIC_RENAMED = {
    "networkMode": "network_mode",
    "extraHosts": "extra_hosts",
    "workingDir": "working_dir",
    "shmSize": "shm_size",
    "stdinOpen": "stdin_open",
    "extraLabels": "labels",
    "capAdd": "cap_add",
    "capDrop": "cap_drop",
    "securityOpt": "security_opt",
    "readOnly": "read_only",
    "stopSignal": "stop_signal",
    "stopGracePeriod": "stop_grace_period",
    "dependsOn": "depends_on",
}


def runtipi_dynamic_to_compose(data: dict) -> dict:
    """A Runtipi docker-compose.json as a compose file, the way Runtipi converts it
    (convertLegacyToYaml). The web port is published from x-runtipi afterwards."""
    services = {}
    for item in data.get("services") or []:
        service: dict = {"restart": "unless-stopped"}
        for key, value in item.items():
            if key in _DYNAMIC_SAME or key in _DYNAMIC_RENAMED:
                service[_DYNAMIC_RENAMED.get(key, key)] = value
        if ports := [p for port in item.get("addPorts") or [] for p in _ports(port)]:
            service["ports"] = ports
        if env := item.get("environment"):
            pairs = (
                [(e["key"], e["value"]) for e in env]
                if isinstance(env, list)
                else env.items()
            )
            # compose takes no booleans: Runtipi writes them as text
            service["environment"] = {
                k: str(v).lower() if isinstance(v, bool) else v for k, v in pairs
            }
        if volumes := item.get("volumes"):
            service["volumes"] = [_volume(v) for v in volumes]
        for key, target in (("healthCheck", "healthcheck"), ("deploy", "deploy")):
            if item.get(key):
                service[target] = _snake_keys(item[key])
        meta = {
            "is_main": item.get("isMain"),
            "internal_port": item.get("internalPort"),
        }
        if meta := {k: v for k, v in meta.items() if v is not None}:
            service["x-runtipi"] = meta
        services[item["name"]] = service
    return {"services": services}


def _ports(port: dict) -> list[str]:
    """`addPorts` entry: one mapping per protocol, none means both."""
    mapping = f"{port['hostPort']}:{port['containerPort']}"
    if port.get("interface"):
        mapping = f"{port['interface']}:{mapping}"
    protocols = [p for p in ("tcp", "udp") if port.get(p)]
    return [f"{mapping}/{p}" for p in protocols] or [mapping]


def _volume(volume: dict) -> str:
    options = [
        option
        for option, on in (
            ("ro", volume.get("readOnly")),
            ("z", volume.get("shared")),
            ("Z", volume.get("private") and not volume.get("shared")),
            ((volume.get("bind") or {}).get("propagation"), True),
        )
        if on and option
    ]
    mapping = f"{volume['hostPath']}:{volume['containerPath']}"
    return f"{mapping}:{','.join(options)}" if options else mapping


def _snake_keys(value):
    """startPeriod is start_period, deviceIds is device_ids, at every level."""
    if isinstance(value, dict):
        return {
            re.sub(r"(?<!^)([A-Z])", r"_\1", k).lower(): _snake_keys(v)
            for k, v in value.items()
        }
    if isinstance(value, list):
        return [_snake_keys(v) for v in value]
    return value


def render_runtipi(app: StoreApp, project: str) -> dict:
    data = safe_load(app.compose or "") or {}
    data.pop("version", None)
    data["name"] = project
    for service in (data.get("services") or {}).values():
        if not isinstance(service, dict):
            continue
        networks = service.get("networks")
        if isinstance(networks, list):
            networks = [n for n in networks if n != "tipi_main_network"]
        elif isinstance(networks, dict):
            networks.pop("tipi_main_network", None)
        if networks:
            service["networks"] = networks
        else:
            service.pop("networks", None)
        labels = service.get("labels")
        if isinstance(labels, dict):
            service["labels"] = {
                k: v
                for k, v in labels.items()
                if not str(k).startswith(("traefik.", "runtipi."))
            }
            if not service["labels"]:
                service.pop("labels")
    networks = data.get("networks")
    if isinstance(networks, dict):
        networks.pop("tipi_main_network", None)
        if not networks:
            data.pop("networks")
    return data


# --- render ---


def render_files(app: StoreApp, env: dict[str, str]) -> dict[str, str]:
    """The data files of an app (path in the data folder: text), as Runtipi writes
    them: a .template file gets the {{VAR}} values and loses its suffix."""
    return {
        path.removesuffix(".template"): (
            _TEMPLATE_VAR.sub(lambda m: env.get(m[1], ""), text)
            if path.endswith(".template")
            else text
        )
        for path, text in app.files.items()
    }


def generate_secret(length: int = 32) -> str:
    alphabet = string.ascii_letters + string.digits
    return "".join(secrets.choice(alphabet) for _ in range(max(length, 16)))


def render_app(
    app: StoreApp,
    project: str,
    data_dir: str,
    data_root: str,
    values: dict[str, str],
    defaults: dict[str, str],
    rename_containers: bool = True,
) -> tuple[str, dict[str, str]]:
    """Return (compose text, env values) for an install or package update.

    `rename_containers` is off for package updates of apps installed before fixed
    container names were renamed, so their containers keep their names.
    """
    env = dict(defaults)
    if app.kind == KIND_CASAOS:
        data = render_casaos(app, project, data_dir, data_root)
        env["AppID"] = project
        if port := values.get("APP_PORT"):
            publish_web_port(data, app, str(port))
    else:
        data = render_runtipi(app, project)
        env.update(
            {
                "APP_DATA_DIR": data_dir,
                "ROOT_FOLDER_HOST": data_root,
                "APP_PORT": str(values.get("APP_PORT") or app.web_port or ""),
                "APP_DOMAIN": values.get("APP_DOMAIN") or "localhost",
                "LOCAL_DOMAIN": values.get("LOCAL_DOMAIN") or "local",
                "APP_PROTOCOL": "http",
                "APP_EXPOSED": "false",
            }
        )
    if rename_containers and project not in (app.app_id, safe_id(app.name)):
        _rename_containers(data, app.app_id, project)
    _drop_desktop_http_port(data)
    for variable in app.variables:
        value = values.get(variable.key)
        if value is None:
            value = (
                generate_secret(variable.min or 32)
                if variable.type == "random"
                else variable.default or ""
            )
        env[variable.key] = str(value)
    for key, value in values.items():
        env.setdefault(key, str(value))
    return yaml.safe_dump(data, sort_keys=False, allow_unicode=True), env


def _rename_containers(data: dict, app_id: str, project: str) -> None:
    """Fixed container names of an app installed under another name, so they don't
    clash with a container that already has that name. The old name stays an alias
    in the app's networks, as the services may reach each other by it
    (DB_HOST=app-db).
    """
    for service in (data.get("services") or {}).values():
        old = isinstance(service, dict) and service.get("container_name")
        if not old:
            continue
        # immich-db becomes photos-db; dbgate of the app db becomes photos-dbgate
        service["container_name"] = (
            project + old[len(app_id) :]
            if old == app_id or old.startswith(f"{app_id}-")
            else f"{project}-{old}"
        )
        if service.get("network_mode"):
            continue
        networks = service.get("networks") or ["default"]
        if isinstance(networks, list):
            networks = dict.fromkeys(networks)
        service["networks"] = {
            name: {**(spec or {}), "aliases": [*(spec or {}).get("aliases", []), old]}
            for name, spec in networks.items()
        }


def _drop_desktop_http_port(data: dict) -> None:
    """Don't publish the HTTP port of linuxserver.io desktop images.

    It only shows an error that HTTPS is needed, and it often clashes with other
    apps on 3000; the web UI is the HTTPS port 3001.
    """
    for service in (data.get("services") or {}).values():
        if not isinstance(service, dict):
            continue
        targets = [t for _, t in _port_pairs(service)]
        image = str(service.get("image") or "")
        if image.startswith(LINUXSERVER_IMAGES) and {"3000", "3001"} <= set(targets):
            service["ports"] = [
                port
                for port, target in zip(service["ports"], targets, strict=True)
                if target != "3000"
            ]


def publish_web_port(data: dict, app: StoreApp, port: str) -> None:
    """Publish the web UI on another host port (CasaOS ports are fixed in compose)."""
    service = (data.get("services") or {}).get(app.main_service) or {}
    old = str(app.web_port)
    for index, mapping in enumerate(service.get("ports") or []):
        if isinstance(mapping, dict):
            if str(mapping.get("published")) == old:
                mapping["published"] = port
        else:
            parts = str(mapping).split(":")
            if len(parts) >= 2 and parts[-2] == old:
                parts[-2] = port
                service["ports"][index] = ":".join(parts)


def check_env(env: dict[str, str]) -> None:
    """ValueError for a name or value that would not stay one line of .env: a line
    break would add variables of its own."""
    for key, value in env.items():
        if not isinstance(key, str) or not _ENV_NAME.fullmatch(key):
            raise ValueError(f"'{key}' is not a variable name")
        for char, words in (("\n", "line break"), ("\r", "line break"), ("\0", "NUL")):
            if char in str(value):
                raise ValueError(f"the value of {key} can't contain a {words}")


def format_env(env: dict[str, str]) -> str:
    """A .env file; values with special characters are quoted the dotenv way."""
    check_env(env)
    lines = ["# Generated by Docker Apps for Home Assistant"]
    for key, value in env.items():
        value = str(value)
        if "'" in value:
            # docker compose expands $VAR in double quotes; $$ is a plain $
            escaped = value.replace("\\", "\\\\").replace('"', '\\"')
            value = '"' + escaped.replace("$", "$$") + '"'
        # whitespace too: a tab at the end is stripped, before # it starts a comment
        elif any(c in '#"$' or c.isspace() for c in value):
            value = f"'{value}'"
        lines.append(f"{key}={value}")
    return "\n".join(lines) + "\n"


def published_ports(compose_text: str) -> set[int]:
    services = (safe_load(compose_text) or {}).get("services") or {}
    return {
        int(published)
        for service in services.values()
        for published, _ in _port_pairs(service or {})
        if published.isdigit()
    }


# --- archives ---


def parse_archive(
    raw: bytes | bytearray, kind: str, repo_id: str, raw_url: str | None = None
) -> list[StoreApp]:
    """Read a repository archive (tar.gz or zip) and return its apps.

    `raw_url` is the base for files in the repository (Runtipi app logos).
    """
    files = _read_archive(raw, kind)
    if kind == KIND_CASAOS:
        apps = []
        for path, content in files.items():
            if path.rsplit("/", 1)[-1] in ("docker-compose.yml", "docker-compose.yaml"):
                try:
                    app = parse_casaos_compose(content, repo_id)
                except Exception as err:  # noqa: BLE001
                    # one broken app is left out, the rest of the store loads
                    _LOGGER.debug("App %s left out: %s", path, err)
                    continue
                if app:
                    apps.append(app)
        return apps

    apps = []
    folders = {p.rsplit("/", 1)[0] for p in files if p.endswith("/config.json")}
    for folder in sorted(folders):
        try:
            app = parse_runtipi(
                files[f"{folder}/config.json"],
                files.get(f"{folder}/docker-compose.yml"),
                files.get(f"{folder}/docker-compose.json"),
                repo_id,
                description=files.get(f"{folder}/metadata/description.md", ""),
            )
        except Exception as err:  # noqa: BLE001
            _LOGGER.debug("App %s left out: %s", folder, err)
            continue
        if app:
            data = f"{folder}/data/"
            app.files = {
                p[len(data) :]: text for p, text in files.items() if p.startswith(data)
            }
            if raw_url:
                app.icon = f"{raw_url}/{folder}/metadata/logo.jpg"
            apps.append(app)
    return apps


_WANTED = {
    KIND_CASAOS: ("docker-compose.yml", "docker-compose.yaml"),
    KIND_RUNTIPI: (
        "config.json",
        "docker-compose.yml",
        "docker-compose.json",
        "description.md",
    ),
}
_MAX_FILE = 512 * 1024
# a repository is refused beyond these (Runtipi, the biggest, keeps ~2700 files)
MAX_FILES = 20_000
MAX_TEXT = 100 * 1024 * 1024
MAX_UNPACKED = 2 * 1024 * 1024 * 1024


class ArchiveTooBig(ValueError):
    """A repository archive beyond the limits: refused, not read on."""


def _read_archive(raw: bytes | bytearray, kind: str) -> dict[str, str]:
    files: dict[str, str] = {}
    kept = 0  # bytes of text kept

    def add(name: str, read: Callable[[], bytes | None]) -> None:
        nonlocal kept
        path = _strip_root(name)
        parts = path.split("/")
        # Runtipi: apps/<id>/data/... is copied to the app's data folder; a path
        # that could leave it (data//etc, data/../x) is left out
        data = (
            kind == KIND_RUNTIPI
            and parts[2:3] == ["data"]
            and len(parts) > 3
            and all(p not in ("", ".", "..") for p in parts[3:])
        )
        if parts[-1] not in _WANTED[kind] and not data:
            return
        if len(files) >= MAX_FILES:
            raise ArchiveTooBig(f"repository is too big: more than {MAX_FILES} files")
        try:
            files[path] = (read() or b"").decode(
                "utf-8", "strict" if data else "replace"
            )
        except UnicodeDecodeError:
            return  # a binary data file: left out
        kept += len(files[path])
        if kept > MAX_TEXT:
            raise ArchiveTooBig(
                f"repository is too big: more than {MAX_TEXT // 2**20} MB of files"
            )

    def unpacked(size: int) -> None:
        if size > MAX_UNPACKED:
            raise ArchiveTooBig(
                f"repository is too big: more than {MAX_UNPACKED // 2**30} GB unpacked"
            )

    if raw[:2] == b"PK":
        with zipfile.ZipFile(io.BytesIO(raw)) as archive:
            infos = archive.infolist()
            unpacked(sum(info.file_size for info in infos))
            for info in infos:
                if not info.is_dir() and info.file_size <= _MAX_FILE:
                    add(info.filename, lambda info=info: archive.read(info))
        return files

    with tarfile.open(fileobj=io.BytesIO(raw), mode="r:*") as archive:
        for member in archive:
            # stops a small archive of endless filler before it is all unpacked
            unpacked(member.offset_data + member.size)
            if member.isfile() and member.size <= _MAX_FILE:
                add(
                    member.name,
                    lambda m=member: (f := archive.extractfile(m)) and f.read(),
                )
    return files


def _strip_root(path: str) -> str:
    """Drop the archive's top folder (`repo-main/`)."""
    return path.split("/", 1)[1] if "/" in path else path
