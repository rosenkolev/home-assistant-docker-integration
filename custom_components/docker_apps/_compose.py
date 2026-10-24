"""A small compose engine in Python, on top of the Docker Engine API.

It reads compose files and `.env`, then creates networks, volumes and containers with
the standard `com.docker.compose.*` labels, so `docker compose`, Arcane, Dockge and
Portainer still see the same projects. Covered: the common Compose spec subset
(image, ports, volumes, devices, networks, network_mode, healthcheck, depends_on with
conditions, env_file, profiles, deploy resources, gpus, restart, logging, ...).
Not covered: `build`, `extends`, `include`, swarm-only keys.
"""

# cspell:words cpuset Dockge kmgt memswap nocopy Pids pstart Rootfs Sysctls tmpfs Ulimits Userns

from __future__ import annotations

import asyncio
import hashlib
import json
import posixpath
import re
import shlex
from collections.abc import Callable
from dataclasses import dataclass

import yaml

from ._docker_client import DockerClient, DockerError
from ._models import (
    LABEL_CONFIG_FILES,
    LABEL_NUMBER,
    LABEL_ONEOFF,
    LABEL_PROJECT,
    LABEL_SERVICE,
    LABEL_WORKING_DIR,
)
from .const import _LOGGER

LABEL_CONFIG_HASH = "com.docker.compose.config-hash"
LABEL_ENV_FILE = "com.docker.compose.project.environment_file"
LABEL_VERSION = "com.docker.compose.version"
LABEL_NETWORK = "com.docker.compose.network"
LABEL_VOLUME = "com.docker.compose.volume"
LABEL_DEPENDS_ON = "com.docker.compose.depends_on"
ENGINE_VERSION = "docker-apps"
HEALTH_TIMEOUT = 600
SYSTEMPATHS_UNCONFINED = "systempaths=unconfined"
# name of a new container until the old one is removed
NEW_SUFFIX = "-docker-apps-new"

_VAR = re.compile(
    r"\$(?:(?P<escaped>\$)|\{(?P<braced>[^}]+)\}|(?P<named>[A-Za-z_][A-Za-z0-9_]*))"
)
_DURATION = re.compile(r"(\d+(?:\.\d+)?)(ns|us|ms|s|m|h)")
_DURATION_NS = {"ns": 1, "us": 1e3, "ms": 1e6, "s": 1e9, "m": 60e9, "h": 3600e9}
_SIZE = re.compile(r"^(\d+(?:\.\d+)?)\s*([kmgt]?)i?b?$", re.IGNORECASE)
_SIZE_MUL = {"": 1, "k": 1024, "m": 1024**2, "g": 1024**3, "t": 1024**4}


class ComposeError(Exception):
    """The compose project can't be read or applied."""


# --- reading ---


def parse_env_file(text: str) -> dict[str, str]:
    values = {}
    for line in text.splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        if line.startswith("export "):
            line = line[7:].lstrip()
        key, sep, value = line.partition("=")
        if not sep:
            continue
        value = value.strip()
        if len(value) >= 2 and value[0] == value[-1] and value[0] in "'\"":
            value = value[1:-1]
        elif " #" in value:
            value = value.split(" #", 1)[0].rstrip()
        values[key.strip()] = value
    return values


def interpolate(value, env: dict[str, str]):
    """Replace ${VAR}, ${VAR:-default}, ${VAR-default}, ${VAR:?err} and $VAR."""
    if isinstance(value, dict):
        return {k: interpolate(v, env) for k, v in value.items()}
    if isinstance(value, list):
        return [interpolate(v, env) for v in value]
    if not isinstance(value, str) or "$" not in value:
        return value

    def replace(match: re.Match) -> str:
        if match["escaped"]:
            return "$"
        if match["named"]:
            return env.get(match["named"], "")
        expr = match["braced"]
        for op in (":-", "-", ":?", "?", ":+", "+"):
            if op in expr:
                name, arg = expr.split(op, 1)
                current = env.get(name)
                unset = current is None or (op.startswith(":") and current == "")
                if op in (":-", "-"):
                    return interpolate(arg, env) if unset else current
                if op in (":?", "?"):
                    if unset:
                        raise ComposeError(f"variable {name} is required: {arg}")
                    return current
                return "" if unset else interpolate(arg, env)
        return env.get(expr, "")

    return _VAR.sub(replace, value)


def merge(base, override):
    """Merge compose files like `docker compose -f a -f b`."""
    if isinstance(base, dict) and isinstance(override, dict):
        result = dict(base)
        for key, value in override.items():
            if key in result and key in (
                "ports",
                "expose",
                "dns",
                "dns_search",
                "tmpfs",
            ):
                result[key] = list(
                    dict.fromkeys([*_as_list(result[key]), *_as_list(value)])
                )
            elif (
                key in result
                and key in ("volumes", "devices")
                and isinstance(value, list)
            ):
                result[key] = _merge_by_target(result[key], value)
            elif key in result and key in ("environment", "labels"):
                # a list (`- KEY=value`) and a map merge by key
                result[key] = {**as_mapping(result[key]), **as_mapping(value)}
            else:
                result[key] = merge(result.get(key), value) if key in result else value
        return result
    return override


def as_mapping(value) -> dict:
    """Compose environment or labels as a dict; `KEY` alone maps to None."""
    if isinstance(value, dict):
        return dict(value)
    result = {}
    for item in value or []:
        key, sep, val = str(item).partition("=")
        result[key] = val if sep else None
    return result


def _as_list(value) -> list:
    return value if isinstance(value, list) else [value]


def _merge_by_target(base: list, override: list) -> list:
    def target(item):
        if isinstance(item, dict):
            return item.get("target")
        parts = str(item).split(":")
        return parts[1] if len(parts) > 1 else parts[0]

    result = {target(i): i for i in base}
    result.update({target(i): i for i in override})
    return list(result.values())


@dataclass
class ComposeModel:
    project: str
    working_dir: str  # host path
    config_files: list[str]  # host paths
    env_file: str | None
    services: dict[str, dict]
    networks: dict[str, dict]
    volumes: dict[str, dict]

    def resolve(self, path: str) -> str:
        """Resolve a host path relative to the project folder."""
        return posixpath.normpath(posixpath.join(self.working_dir, path))


def load_model(
    project: str,
    working_dir: str,
    files: dict[str, str],
    env_text: str | None,
    env_file: str | None = None,
    read_file: Callable[[str], str | None] | None = None,
) -> ComposeModel:
    """Build the model from compose file contents (keyed by host path)."""
    env = parse_env_file(env_text) if env_text else {}
    data: dict = {}
    for name, text in files.items():
        loaded = yaml.safe_load(text) or {}
        if not isinstance(loaded, dict):
            raise ComposeError(f"{name} is not a compose file")
        for key in ("extends", "include"):
            if key in loaded:
                raise ComposeError(f"'{key}' is not supported ({name})")
        data = merge(data, loaded)

    data = interpolate(data, env)
    project = data.get("name") or project
    services = {}
    for name, service in (data.get("services") or {}).items():
        service = dict(service or {})
        if service.get("profiles"):
            continue  # profiles are opt-in and none is active
        if "extends" in service:
            raise ComposeError(f"service '{name}': 'extends' is not supported")
        if not service.get("image"):
            raise ComposeError(
                f"service '{name}' has no image ('build' is not supported)"
            )
        services[name] = service

    model = ComposeModel(
        project=project,
        working_dir=working_dir,
        config_files=list(files),
        env_file=env_file,
        services=services,
        networks={k: v or {} for k, v in (data.get("networks") or {}).items()},
        volumes={k: v or {} for k, v in (data.get("volumes") or {}).items()},
    )

    if read_file is not None:
        for service in services.values():
            service["_env_file_values"] = _read_env_files(model, service, read_file)
    return model


def _read_env_files(model: ComposeModel, service: dict, read_file) -> dict[str, str]:
    values: dict[str, str] = {}
    entries = service.get("env_file") or []
    for entry in entries if isinstance(entries, list) else [entries]:
        path, required = (
            (entry, True)
            if isinstance(entry, str)
            else (
                entry.get("path"),
                entry.get("required", True),
            )
        )
        text = read_file(model.resolve(path))
        if text is None:
            if required:
                raise ComposeError(f"env_file {path} not found")
            continue
        values.update(parse_env_file(text))
    return values


# --- conversion to Docker API ---


def duration_ns(value) -> int | None:
    if value is None:
        return None
    if isinstance(value, (int, float)):
        return int(value * 1e9)
    total = sum(float(n) * _DURATION_NS[u] for n, u in _DURATION.findall(str(value)))
    return int(total)


def size_bytes(value) -> int | None:
    if value is None:
        return None
    if isinstance(value, (int, float)):
        return int(value)
    match = _SIZE.match(str(value).strip())
    if not match:
        raise ComposeError(f"invalid size '{value}'")
    return int(float(match[1]) * _SIZE_MUL[match[2].lower()])


def _split_command(value) -> list[str] | None:
    if value is None:
        return None
    if isinstance(value, list):
        return [str(v) for v in value]
    return shlex.split(str(value))


def _environment(service: dict) -> list[str]:
    values = dict(service.get("_env_file_values") or {})
    env = service.get("environment") or {}
    if isinstance(env, list):
        for item in env:
            key, sep, value = str(item).partition("=")
            if sep:
                values[key] = value
            else:
                values.pop(key, None)
    else:
        for key, value in env.items():
            if value is None:
                values.pop(key, None)
            else:
                values[key] = (
                    ("true" if value else "false")
                    if isinstance(value, bool)
                    else str(value)
                )
    return [f"{k}={v}" for k, v in values.items()]


def _labels(value) -> dict[str, str]:
    if isinstance(value, list):
        return dict((item.split("=", 1) + [""])[:2] for item in (str(i) for i in value))
    return {k: str(v) for k, v in (value or {}).items()}


def parse_ports(ports: list) -> tuple[dict, dict]:
    """Return (ExposedPorts, PortBindings)."""
    exposed: dict = {}
    bindings: dict = {}
    for port in ports or []:
        if isinstance(port, dict):
            target = str(port["target"])
            protocol = port.get("protocol", "tcp")
            published = str(port.get("published") or "")
            host_ip = port.get("host_ip", "")
            pairs = [(target, published)]
        else:
            spec, _, protocol = str(port).partition("/")
            protocol = protocol or "tcp"
            parts = spec.rsplit(":", 2)
            if len(parts) == 3:
                host_ip, published, target = parts
            elif len(parts) == 2:
                host_ip, (published, target) = "", parts
            else:
                host_ip, published, target = "", "", parts[0]
            host_ip = host_ip.strip("[]")
            pairs = _expand_range(target, published)
        for target, published in pairs:
            key = f"{target}/{protocol}"
            exposed[key] = {}
            bindings.setdefault(key, []).append(
                {"HostIp": host_ip, "HostPort": published}
            )
    return exposed, bindings


def _expand_range(target: str, published: str) -> list[tuple[str, str]]:
    if "-" not in target:
        return [(target, published)]
    start, end = (int(x) for x in target.split("-"))
    if published and "-" in published:
        pstart = int(published.split("-")[0])
        return [(str(start + i), str(pstart + i)) for i in range(end - start + 1)]
    return [(str(p), published) for p in range(start, end + 1)]


def _volume_name(model: ComposeModel, name: str) -> str:
    spec = model.volumes.get(name) or {}
    if spec.get("name"):
        return spec["name"]
    if spec.get("external"):
        return name
    return f"{model.project}_{name}"


def parse_volumes(
    model: ComposeModel, service: dict
) -> tuple[list[str], dict, list[dict]]:
    """Return (Binds, anonymous Volumes, Mounts)."""
    binds: list[str] = []
    anonymous: dict = {}
    mounts: list[dict] = []
    for volume in service.get("volumes") or []:
        if isinstance(volume, dict):
            kind = volume.get("type", "volume")
            source = volume.get("source")
            target = volume["target"]
            mode = "ro" if volume.get("read_only") else "rw"
            if kind == "tmpfs":
                size = size_bytes((volume.get("tmpfs") or {}).get("size"))
                mounts.append(
                    {"Type": "tmpfs", "Target": target}
                    | ({"TmpfsOptions": {"SizeBytes": size}} if size else {})
                )
                continue
            if kind == "bind":
                binds.append(f"{model.resolve(source)}:{target}:{mode}")
                continue
            if not source:
                anonymous[target] = {}
                continue
            options = [mode]
            if (volume.get("volume") or {}).get("nocopy"):
                options.append("nocopy")
            binds.append(f"{_volume_name(model, source)}:{target}:{','.join(options)}")
            continue

        parts = str(volume).split(":")
        if len(parts) == 1:
            anonymous[parts[0]] = {}
            continue
        source, target = parts[0], parts[1]
        mode = parts[2] if len(parts) > 2 else "rw"
        if source.startswith(("/", ".")):
            binds.append(f"{model.resolve(source)}:{target}:{mode}")
        else:
            binds.append(f"{_volume_name(model, source)}:{target}:{mode}")
    return binds, anonymous, mounts


def parse_devices(devices: list) -> list[dict]:
    result = []
    for device in devices or []:
        if isinstance(device, dict):
            result.append(
                {
                    "PathOnHost": device["source"],
                    "PathInContainer": device.get("target", device["source"]),
                    "CgroupPermissions": device.get("permissions", "rwm"),
                }
            )
            continue
        parts = str(device).split(":")
        result.append(
            {
                "PathOnHost": parts[0],
                "PathInContainer": parts[1] if len(parts) > 1 else parts[0],
                "CgroupPermissions": parts[2] if len(parts) > 2 else "rwm",
            }
        )
    return result


def parse_healthcheck(health: dict | None) -> dict | None:
    if not health:
        return None
    if health.get("disable"):
        return {"Test": ["NONE"]}
    test = health.get("test")
    if isinstance(test, str):
        test = ["CMD-SHELL", test]
    result = {"Test": test}
    for key, api in (
        ("interval", "Interval"),
        ("timeout", "Timeout"),
        ("start_period", "StartPeriod"),
        ("start_interval", "StartInterval"),
    ):
        if health.get(key) is not None:
            result[api] = duration_ns(health[key])
    if health.get("retries") is not None:
        result["Retries"] = int(health["retries"])
    return result


def parse_restart(value) -> dict:
    if not value or value == "no":
        return {"Name": "no"}
    name, _, retries = str(value).partition(":")
    policy = {"Name": name}
    if retries:
        policy["MaximumRetryCount"] = int(retries)
    return policy


def depends_on(service: dict) -> dict[str, str]:
    value = service.get("depends_on") or {}
    if isinstance(value, list):
        return {name: "service_started" for name in value}
    return {
        name: (spec or {}).get("condition", "service_started")
        for name, spec in value.items()
    }


def service_networks(model: ComposeModel, service: dict) -> dict[str, dict]:
    """Return {network name in Docker: endpoint config} for a service."""
    if service.get("network_mode"):
        return {}
    value = service.get("networks")
    if value is None:
        value = {"default": {}}
    if isinstance(value, list):
        value = {name: {} for name in value}
    result = {}
    for name, spec in value.items():
        spec = spec or {}
        endpoint: dict = {"Aliases": [*spec.get("aliases", [])]}
        ipam = {}
        if spec.get("ipv4_address"):
            ipam["IPv4Address"] = spec["ipv4_address"]
        if spec.get("ipv6_address"):
            ipam["IPv6Address"] = spec["ipv6_address"]
        if ipam:
            endpoint["IPAMConfig"] = ipam
        result[network_name(model, name)] = endpoint
    return result


def network_name(model: ComposeModel, name: str) -> str:
    spec = model.networks.get(name) or {}
    if spec.get("name"):
        return spec["name"]
    if spec.get("external"):
        return name
    return f"{model.project}_{name}"


def container_name(model: ComposeModel, service_name: str) -> str:
    service = model.services[service_name]
    return service.get("container_name") or f"{model.project}-{service_name}-1"


def build_container_config(
    model: ComposeModel,
    service_name: str,
    containers: dict[str, str] | None = None,
) -> dict:
    """Return the /containers/create body for one service container.

    `containers` maps service names to container ids for `network_mode: service:x`.
    """
    service = model.services[service_name]
    exposed, bindings = parse_ports(service.get("ports"))
    for port in service.get("expose") or []:
        spec = str(port)
        exposed.setdefault(spec if "/" in spec else f"{spec}/tcp", {})
    binds, anonymous, mounts = parse_volumes(model, service)

    deploy = service.get("deploy") or {}
    resources = deploy.get("resources") or {}
    limits = resources.get("limits") or {}
    reservations = resources.get("reservations") or {}

    labels = _labels(service.get("labels"))
    labels.update(
        {
            LABEL_PROJECT: model.project,
            LABEL_SERVICE: service_name,
            LABEL_NUMBER: "1",
            LABEL_ONEOFF: "False",
            LABEL_WORKING_DIR: model.working_dir,
            LABEL_CONFIG_FILES: ",".join(model.config_files),
            LABEL_VERSION: ENGINE_VERSION,
        }
    )
    if model.env_file:
        labels[LABEL_ENV_FILE] = model.env_file
    deps = depends_on(service)
    if deps:
        labels[LABEL_DEPENDS_ON] = ",".join(f"{k}:{v}:false" for k, v in deps.items())

    host: dict = {
        "Binds": binds,
        "PortBindings": bindings,
        "RestartPolicy": parse_restart(service.get("restart")),
        "Devices": parse_devices(service.get("devices")),
        "CapAdd": service.get("cap_add"),
        "CapDrop": service.get("cap_drop"),
        "Privileged": bool(service.get("privileged")),
        "SecurityOpt": _security_opt(service),
        "ExtraHosts": _extra_hosts(service.get("extra_hosts")),
        "Dns": _as_list(service["dns"]) if service.get("dns") else None,
        "DnsSearch": (
            _as_list(service["dns_search"]) if service.get("dns_search") else None
        ),
        "GroupAdd": [str(g) for g in service.get("group_add") or []] or None,
        "Init": service.get("init"),
        "ReadonlyRootfs": bool(service.get("read_only")),
        "IpcMode": service.get("ipc"),
        "PidMode": service.get("pid"),
        "UsernsMode": service.get("userns_mode"),
        "Sysctls": _labels(service.get("sysctls")) or None,
        "Ulimits": _ulimits(service.get("ulimits")),
        "ShmSize": size_bytes(service.get("shm_size")),
        "Memory": size_bytes(service.get("mem_limit") or limits.get("memory")),
        "MemoryReservation": size_bytes(
            service.get("mem_reservation") or reservations.get("memory")
        ),
        "MemorySwap": size_bytes(service.get("memswap_limit")),
        "NanoCpus": int(float(service.get("cpus") or limits.get("cpus") or 0) * 1e9)
        or None,
        "CpuShares": service.get("cpu_shares"),
        "CpusetCpus": service.get("cpuset"),
        "PidsLimit": service.get("pids_limit") or limits.get("pids"),
        "OomScoreAdj": service.get("oom_score_adj"),
        "DeviceRequests": _device_requests(service, reservations),
        "VolumesFrom": service.get("volumes_from"),
    }
    if mounts:
        host["Mounts"] = mounts
    if tmpfs := service.get("tmpfs"):
        host["Tmpfs"] = {
            (t.split(":", 1) + [""])[0]: (t.split(":", 1) + [""])[1]
            for t in _as_list(tmpfs)
        }
    if logging := service.get("logging"):
        host["LogConfig"] = {
            "Type": logging.get("driver", "json-file"),
            "Config": {k: str(v) for k, v in (logging.get("options") or {}).items()},
        }

    networks = service_networks(model, service)
    mode = service.get("network_mode")
    if mode and mode.startswith("service:"):
        target = mode.split(":", 1)[1]
        if not containers or target not in containers:
            raise ComposeError(f"network_mode {mode}: service is not running")
        host["NetworkMode"] = f"container:{containers[target]}"
    elif mode:
        host["NetworkMode"] = mode
    elif networks:
        host["NetworkMode"] = next(iter(networks))

    config: dict = {
        "Image": service["image"],
        "Cmd": _split_command(service.get("command")),
        "Entrypoint": _split_command(service.get("entrypoint")),
        "Env": _environment(service),
        "Labels": labels,
        "User": str(service["user"]) if service.get("user") is not None else None,
        "WorkingDir": service.get("working_dir"),
        "Hostname": service.get("hostname"),
        "Domainname": service.get("domainname"),
        "Tty": bool(service.get("tty")),
        "OpenStdin": bool(service.get("stdin_open")),
        "StopSignal": service.get("stop_signal"),
        "StopTimeout": (
            int(duration_ns(service["stop_grace_period"]) / 1e9)
            if service.get("stop_grace_period")
            else None
        ),
        "Healthcheck": parse_healthcheck(service.get("healthcheck")),
        "ExposedPorts": exposed or None,
        "Volumes": anonymous or None,
        "MacAddress": service.get("mac_address"),
        "HostConfig": _drop_none(host),
    }
    if networks and "NetworkMode" in host and not mode:
        first = next(iter(networks))
        endpoint = dict(networks[first])
        endpoint["Aliases"] = list(
            dict.fromkeys([service_name, *endpoint.get("Aliases", [])])
        )
        config["NetworkingConfig"] = {"EndpointsConfig": {first: endpoint}}

    config = _drop_none(config)
    if SYSTEMPATHS_UNCONFINED in (service.get("security_opt") or []):
        # set after _drop_none: empty lists are the point here
        config["HostConfig"]["MaskedPaths"] = []
        config["HostConfig"]["ReadonlyPaths"] = []
    config["Labels"][LABEL_CONFIG_HASH] = config_hash(config)
    return config


def _security_opt(service: dict) -> list[str] | None:
    """Security options for the API; `systempaths=unconfined` is CLI-only.

    The docker CLI turns it into empty MaskedPaths and ReadonlyPaths instead.
    """
    options = [
        str(o) for o in service.get("security_opt") or [] if o != SYSTEMPATHS_UNCONFINED
    ]
    return options or None


def config_hash(config: dict) -> str:
    data = json.dumps(
        {k: v for k, v in config.items() if k != "Labels"}
        | {
            "Labels": {
                k: v
                for k, v in config.get("Labels", {}).items()
                if k != LABEL_CONFIG_HASH
            }
        },
        sort_keys=True,
        default=str,
    )
    return hashlib.sha256(data.encode()).hexdigest()


def _drop_none(data: dict) -> dict:
    return {k: v for k, v in data.items() if v is not None and v != [] and v != {}}


def _extra_hosts(value) -> list[str] | None:
    if not value:
        return None
    if isinstance(value, dict):
        return [f"{k}:{v}" for k, v in value.items()]
    return [str(v).replace("=", ":", 1) for v in value]


def _ulimits(value) -> list[dict] | None:
    if not value:
        return None
    result = []
    for name, limit in value.items():
        if isinstance(limit, dict):
            result.append(
                {"Name": name, "Soft": int(limit["soft"]), "Hard": int(limit["hard"])}
            )
        else:
            result.append({"Name": name, "Soft": int(limit), "Hard": int(limit)})
    return result


def _device_requests(service: dict, reservations: dict) -> list[dict] | None:
    """GPU and other device reservations; no count and no ids means all devices."""
    requests = []
    for device in reservations.get("devices") or []:
        count = device.get("count")
        if count == "all" or (count is None and not device.get("device_ids")):
            count = -1
        requests.append(
            _drop_none(
                {
                    "Driver": device.get("driver"),
                    "Count": count,
                    "DeviceIDs": device.get("device_ids"),
                    "Capabilities": [device.get("capabilities") or ["gpu"]],
                    "Options": device.get("options"),
                }
            )
        )
    if gpus := service.get("gpus"):
        count = -1 if gpus == "all" else int(gpus)
        requests.append({"Count": count, "Capabilities": [["gpu"]]})
    return requests or None


def start_order(model: ComposeModel, services: list[str] | None = None) -> list[str]:
    """Services sorted so dependencies come first."""
    wanted = list(services or model.services)
    order: list[str] = []
    visiting: set[str] = set()

    def visit(name: str):
        if name in order:
            return
        if name in visiting:
            raise ComposeError(f"dependency cycle at service '{name}'")
        visiting.add(name)
        for dep in depends_on(model.services.get(name, {})):
            if dep in model.services:
                visit(dep)
        visiting.discard(name)
        order.append(name)

    for name in wanted:
        visit(name)
    return order


# --- applying ---


Progress = Callable[[str, float | None], None]


class ComposeEngine:
    """Apply a ComposeModel through the Docker API."""

    def __init__(self, client: DockerClient):
        self.client = client

    async def _project_containers(self, project: str) -> list[dict]:
        return [
            c
            for c in await self.client.async_containers(all=True)
            if (c.get("Labels") or {}).get(LABEL_PROJECT) == project
            and (c.get("Labels") or {}).get(LABEL_ONEOFF) != "True"
        ]

    async def async_pull(
        self, model: ComposeModel, progress: Progress | None = None
    ) -> None:
        images = list(
            dict.fromkeys(
                service["image"]
                for service in model.services.values()
                if service.get("pull_policy") not in ("never", "build")
            )
        )
        for index, image in enumerate(images):
            if progress:
                progress(f"Pulling {image}", index / max(len(images), 1))
            async for _ in self.client.async_image_pull(image):
                pass

    async def _ensure_networks(self, model: ComposeModel, names: set[str]) -> None:
        existing = {n["Name"] for n in await self.client.async_networks()}
        for key in {"default", *model.networks}:
            name = network_name(model, key)
            if name not in names or name in existing:
                continue
            spec = model.networks.get(key) or {}
            if spec.get("external"):
                raise ComposeError(f"external network {name} not found")
            body = _drop_none(
                {
                    "Name": name,
                    "Driver": spec.get("driver", "bridge"),
                    "Options": spec.get("driver_opts"),
                    "Internal": spec.get("internal"),
                    "Attachable": spec.get("attachable"),
                    "EnableIPv6": spec.get("enable_ipv6"),
                    "Labels": {
                        **_labels(spec.get("labels")),
                        LABEL_PROJECT: model.project,
                        LABEL_NETWORK: key,
                        LABEL_VERSION: ENGINE_VERSION,
                    },
                }
            )
            if ipam := spec.get("ipam"):
                body["IPAM"] = {
                    "Driver": ipam.get("driver", "default"),
                    "Config": [
                        _drop_none(
                            {
                                "Subnet": c.get("subnet"),
                                "Gateway": c.get("gateway"),
                                "IPRange": c.get("ip_range"),
                            }
                        )
                        for c in ipam.get("config") or []
                    ],
                }
            await self.client.async_network_create(body)

    async def _ensure_volumes(self, model: ComposeModel) -> None:
        existing = {v["Name"] for v in await self.client.async_volumes()}
        for key, spec in model.volumes.items():
            name = _volume_name(model, key)
            if name in existing:
                continue
            if spec.get("external"):
                raise ComposeError(f"external volume {name} not found")
            await self.client.async_volume_create(
                _drop_none(
                    {
                        "Name": name,
                        "Driver": spec.get("driver", "local"),
                        "DriverOpts": spec.get("driver_opts"),
                        "Labels": {
                            **_labels(spec.get("labels")),
                            LABEL_PROJECT: model.project,
                            LABEL_VOLUME: key,
                            LABEL_VERSION: ENGINE_VERSION,
                        },
                    }
                )
            )

    async def _image_id(self, image: str) -> str:
        info = await self.client.async_image_inspect(image)
        if info is None:
            async for _ in self.client.async_image_pull(image):
                pass
            info = await self.client.async_image_inspect(image)
        if info is None:
            raise ComposeError(f"image {image} not found")
        return info["Id"]

    async def _wait_for(self, container_id: str, condition: str, name: str) -> None:
        if condition == "service_started":
            return
        loop = asyncio.get_running_loop()
        deadline = loop.time() + HEALTH_TIMEOUT
        while loop.time() < deadline:
            state = (await self.client.async_container_inspect(container_id)).get(
                "State"
            ) or {}
            if condition == "service_healthy":
                health = (state.get("Health") or {}).get("Status")
                if health == "healthy" or (health is None and state.get("Running")):
                    return
                if health == "unhealthy":
                    raise ComposeError(f"dependency {name} is unhealthy")
            elif condition == "service_completed_successfully":
                if state.get("Status") == "exited":
                    if state.get("ExitCode") == 0:
                        return
                    raise ComposeError(f"dependency {name} failed")
            if (
                state.get("Status") in ("exited", "dead")
                and condition == "service_healthy"
            ):
                raise ComposeError(f"dependency {name} stopped")
            await asyncio.sleep(2)
        raise ComposeError(f"timeout waiting for {name} ({condition})")

    async def async_up(
        self,
        model: ComposeModel,
        services: list[str] | None = None,
        force_recreate: bool = False,
        remove_orphans: bool = True,
        progress: Progress | None = None,
        skip: set[str] | None = None,
    ) -> None:
        """Create or update the project, like `docker compose up -d`."""
        order = start_order(model, services)
        await self._ensure_volumes(model)
        needed = set()
        for name in order:
            needed.update(service_networks(model, model.services[name]))
        await self._ensure_networks(model, needed)

        existing = await self._project_containers(model.project)
        by_service: dict[str, list[dict]] = {}
        for raw in existing:
            by_service.setdefault(raw["Labels"].get(LABEL_SERVICE), []).append(raw)

        running: dict[str, str] = {}
        for index, name in enumerate(order):
            if progress:
                progress(f"Starting {name}", index / max(len(order), 1))
            for dep, condition in depends_on(model.services[name]).items():
                if dep in running:
                    await self._wait_for(running[dep], condition, dep)
            if skip and name in skip:
                continue
            running[name] = await self._apply_service(
                model, name, by_service.get(name, []), running, force_recreate
            )

        if remove_orphans and services is None:
            for service, containers in by_service.items():
                if service not in model.services:
                    for raw in containers:
                        _LOGGER.info("Removing orphan container %s", raw["Names"][0])
                        await self.async_remove(raw["Id"])

    async def _apply_service(
        self,
        model: ComposeModel,
        name: str,
        current: list[dict],
        running: dict[str, str],
        force: bool,
    ) -> str:
        config = build_container_config(model, name, running)
        image_id = await self._image_id(config["Image"])
        wanted_hash = config["Labels"][LABEL_CONFIG_HASH]

        target = container_name(model, name)
        temporary = f"{target}{NEW_SUFFIX}"
        keep = None
        old = []
        for raw in current:
            labels = raw.get("Labels") or {}
            if raw.get("Names") == [f"/{temporary}"]:
                # left over from an earlier run that failed half way
                await self.async_remove(raw["Id"])
            elif (
                not force
                and keep is None
                and labels.get(LABEL_CONFIG_HASH) == wanted_hash
                and raw.get("ImageID") == image_id
            ):
                keep = raw
            else:
                old.append(raw)

        if keep is not None:
            for raw in old:
                await self.async_remove(raw["Id"])
            if keep.get("State") != "running":
                await self.client.async_container_action(keep["Id"], "start")
            return keep["Id"]

        # create before removing the old container: a config that Docker refuses
        # leaves the running app as it is
        container_id = await self.client.async_container_create(
            temporary if old else target, config
        )
        for raw in old:
            await self.async_remove(raw["Id"])
        if old:
            await self.client.async_container_rename(container_id, target)
        networks = service_networks(model, model.services[name])
        for extra in list(networks)[1:]:
            endpoint = dict(networks[extra])
            endpoint["Aliases"] = list(
                dict.fromkeys([name, *endpoint.get("Aliases", [])])
            )
            await self.client.async_network_connect(extra, container_id, endpoint)
        await self.client.async_container_action(container_id, "start")
        return container_id

    async def async_remove(self, container_id: str, volumes: bool = False) -> None:
        """Stop gracefully (honors the stop timeout), then remove."""
        try:
            await self.client.async_container_action(container_id, "stop")
        except DockerError as err:
            if err.status not in (304, 404):
                raise
        await self.client.async_container_remove(container_id, volumes)

    async def async_down(self, model: ComposeModel, volumes: bool = False) -> None:
        """Remove the containers and networks of the project, and its volumes if asked."""
        for raw in reversed(await self._project_containers(model.project)):
            await self.async_remove(raw["Id"], volumes)
        for network in await self.client.async_networks():
            if (network.get("Labels") or {}).get(LABEL_PROJECT) == model.project:
                try:
                    await self.client.async_network_remove(network["Id"])
                except DockerError as err:
                    _LOGGER.debug("Network %s kept: %s", network["Name"], err)
        if volumes:
            for volume in await self.client.async_volumes():
                if (volume.get("Labels") or {}).get(LABEL_PROJECT) == model.project:
                    await self.client.async_volume_remove(volume["Name"])
