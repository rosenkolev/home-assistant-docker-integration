"""App store repositories: CasaOS and Runtipi, read from repository archives.

Every source is turned into the same normalized `StoreApp`. Rendering an app gives a
plain compose file plus a `.env`, so installed apps are ordinary compose projects.
"""

# cspell:words armhf armv Selkies

from __future__ import annotations

import hashlib
import io
import json
import re
import secrets
import string
import tarfile
import zipfile
from dataclasses import asdict, dataclass, field

import yaml

from ._models import split_image

KIND_CASAOS = "casaos"
KIND_RUNTIPI = "runtipi"
KINDS = (KIND_CASAOS, KIND_RUNTIPI)

ARCH_ALIASES = {"x86_64": "amd64", "aarch64": "arm64", "armv7l": "arm", "armhf": "arm"}
_GITHUB = re.compile(r"^https://github\.com/([^/]+)/([^/#?]+?)(?:\.git)?/?$")
_SAFE_ID = re.compile(r"[^a-z0-9_-]+")
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

    def as_dict(self, with_compose: bool = True) -> dict:
        data = asdict(self)
        if not with_compose:
            data.pop("compose")
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
    return _SAFE_ID.sub("-", value.lower()).strip("-") or "app"


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


def _localized(value, locale: str = "en_US") -> str:
    if isinstance(value, dict):
        return (
            value.get(locale)
            or value.get("en_US")
            or value.get("en_GB")
            or next(iter(value.values()), "")
        )
    return value or ""


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
    data = yaml.safe_load(text) or {}
    meta = data.get("x-casaos") or {}
    if not meta or not data.get("services"):
        return None
    app_id = safe_id(
        str(meta.get("id") or data.get("name") or next(iter(data["services"])))
    )
    port = str(meta.get("port_map") or "").strip()
    # some stores (BigBear) have no version: show the main image tag instead
    main = (
        data["services"].get(meta.get("main")) or next(iter(data["services"].values()))
    ) or {}
    version = str(
        meta.get("version") or split_image(str(main.get("image", "")))[1] or ""
    )
    web_port, web_target, web_scheme = web_endpoint(
        main, port, str(meta.get("scheme") or "").lower()
    )
    return StoreApp(
        id=f"{repo_id}.{app_id}",
        repo_id=repo_id,
        app_id=app_id,
        kind=KIND_CASAOS,
        name=_localized(meta.get("title")) or app_id,
        version=version,
        package_version=str(meta.get("version") or _hash(text)),
        tagline=_localized(meta.get("tagline")),
        description=_localized(meta.get("description")),
        icon=meta.get("icon"),
        categories=[meta["category"]] if meta.get("category") else [],
        architectures=list(meta.get("architectures") or []),
        website=meta.get("website"),
        source=meta.get("repo"),
        release_notes=_localized(meta.get("release_notes")) or None,
        content_hash=_hash(text),
        main_service=meta.get("main"),
        web_port=web_port,
        web_path=str(meta.get("index") or "/"),
        web_target=web_target,
        web_scheme=web_scheme,
        compose=text,
    )


def render_casaos(app: StoreApp, project: str, data_dir: str, data_root: str) -> dict:
    data = yaml.safe_load(app.compose or "") or {}
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
    return yaml.safe_load(text)


# --- Runtipi ---


def parse_runtipi(
    config_text: str,
    compose_text: str | None,
    dynamic_text: str | None,
    repo_id: str,
    description: str = "",
) -> StoreApp | None:
    config = json.loads(config_text)
    if not config.get("available", True) or config.get("deprecated"):
        return None
    app_id = safe_id(str(config["id"]))
    compose = compose_text or (
        yaml.safe_dump(
            runtipi_dynamic_to_compose(json.loads(dynamic_text)), sort_keys=False
        )
        if dynamic_text
        else None
    )
    if compose is None:
        return None

    variables = [
        StoreVariable(
            key=field_["env_variable"],
            label=field_.get("label") or field_["env_variable"],
            type=field_.get("type", "text"),
            default=(
                str(field_["default"]) if field_.get("default") is not None else None
            ),
            required=bool(field_.get("required")),
            min=field_.get("min"),
        )
        for field_ in config.get("form_fields") or []
        if field_.get("env_variable")
    ]

    services = (yaml.safe_load(compose) or {}).get("services") or {}
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
        name=config.get("name") or app_id,
        version=str(config.get("version") or ""),
        package_version=str(config.get("tipi_version") or "0"),
        tagline=config.get("short_desc") or "",
        description=description or config.get("description") or "",
        categories=list(config.get("categories") or []),
        architectures=list(config.get("supported_architectures") or []),
        website=config.get("website"),
        source=config.get("source"),
        content_hash=_hash(config_text, compose),
        web_port=config.get("port") if web_target != 3001 else web_port,
        web_target=web_target,
        web_scheme=web_scheme,
        variables=variables,
        compose=compose,
    )


def runtipi_dynamic_to_compose(data: dict) -> dict:
    """Convert a Runtipi dynamic compose (schema v2) to a compose file."""
    services = {}
    for item in data.get("services") or []:
        service: dict = {"image": item["image"], "restart": "unless-stopped"}
        if item.get("command"):
            service["command"] = item["command"]
        if env := item.get("environment"):
            service["environment"] = (
                {e["key"]: e["value"] for e in env} if isinstance(env, list) else env
            )
        if volumes := item.get("volumes"):
            service["volumes"] = [
                f"{v['hostPath']}:{v['containerPath']}"
                + (":ro" if v.get("readOnly") else "")
                for v in volumes
            ]
        ports = []
        if item.get("isMain") and item.get("internalPort"):
            ports.append(f"${{APP_PORT}}:{item['internalPort']}")
        for port in item.get("addPorts") or []:
            ports.append(
                f"{port['hostPort']}:{port['containerPort']}"
                + ("/udp" if port.get("udp") else "")
            )
        if ports:
            service["ports"] = ports
        if depends := item.get("dependsOn"):
            service["depends_on"] = depends
        for source, target in (
            ("networkMode", "network_mode"),
            ("privileged", "privileged"),
            ("devices", "devices"),
            ("capAdd", "cap_add"),
            ("user", "user"),
            ("entrypoint", "entrypoint"),
            ("workingDir", "working_dir"),
            ("shmSize", "shm_size"),
        ):
            if item.get(source) is not None:
                service[target] = item[source]
        if health := item.get("healthCheck"):
            service["healthcheck"] = {
                key.replace("startPeriod", "start_period"): value
                for key, value in health.items()
            }
        services[item["name"]] = service
    return {"services": services}


def render_runtipi(app: StoreApp, project: str) -> dict:
    data = yaml.safe_load(app.compose or "") or {}
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
) -> tuple[str, dict[str, str]]:
    """Return (compose text, env values) for an install or package update."""
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


def format_env(env: dict[str, str]) -> str:
    lines = ["# Generated by Docker Apps for Home Assistant"]
    for key, value in env.items():
        value = str(value)
        if any(c in value for c in " #'\"$"):
            value = "'" + value.replace("'", "'\\''") + "'"
        lines.append(f"{key}={value}")
    return "\n".join(lines) + "\n"


def published_ports(compose_text: str) -> set[int]:
    data = yaml.safe_load(compose_text) or {}
    ports = set()
    for service in (data.get("services") or {}).values():
        for port in (service or {}).get("ports") or []:
            if isinstance(port, dict):
                published = str(port.get("published") or "")
            else:
                parts = str(port).split("/")[0].split(":")
                published = parts[-2] if len(parts) >= 2 else ""
            if published.isdigit():
                ports.add(int(published))
    return ports


# --- archives ---


def parse_archive(
    raw: bytes, kind: str, repo_id: str, raw_url: str | None = None
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
                except yaml.YAMLError:
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
        except (ValueError, KeyError, yaml.YAMLError):
            continue
        if app:
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


def _read_archive(raw: bytes, kind: str) -> dict[str, str]:
    wanted = _WANTED[kind]
    files: dict[str, str] = {}
    if raw[:2] == b"PK":
        with zipfile.ZipFile(io.BytesIO(raw)) as archive:
            for info in archive.infolist():
                if (
                    info.filename.rsplit("/", 1)[-1] in wanted
                    and info.file_size <= _MAX_FILE
                ):
                    files[_strip_root(info.filename)] = archive.read(info).decode(
                        "utf-8", "replace"
                    )
        return files

    with tarfile.open(fileobj=io.BytesIO(raw), mode="r:*") as archive:
        for member in archive:
            if not member.isfile() or member.size > _MAX_FILE:
                continue
            if member.name.rsplit("/", 1)[-1] in wanted:
                file = archive.extractfile(member)
                if file:
                    files[_strip_root(member.name)] = file.read().decode(
                        "utf-8", "replace"
                    )
    return files


def _strip_root(path: str) -> str:
    """Drop the archive's top folder (`repo-main/`)."""
    return path.split("/", 1)[1] if "/" in path else path
