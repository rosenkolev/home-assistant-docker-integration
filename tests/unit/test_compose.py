# cspell:words INITDB

import pytest

from custom_components.docker_apps._compose import (
    LABEL_CONFIG_HASH,
    ComposeEngine,
    ComposeError,
    build_container_config,
    duration_ns,
    interpolate,
    load_model,
    parse_env_file,
    parse_ports,
    size_bytes,
    start_order,
)
from custom_components.docker_apps._docker_client import DockerError
from tests.fake_docker import FakeDocker

COMPOSE = """
name: immich
services:
  immich-server:
    container_name: immich_server
    image: ghcr.io/immich-app/immich-server:${IMMICH_VERSION:-release}
    volumes:
      - ${UPLOAD_LOCATION}:/data
      - /etc/localtime:/etc/localtime:ro
    env_file:
      - .env
    ports:
      - "2283:2283"
    depends_on:
      redis:
        condition: service_healthy
      database:
        condition: service_started
    restart: unless-stopped
    healthcheck:
      disable: false
      test: ["CMD", "curl", "-f", "http://localhost:2283"]
      interval: 1m30s
      retries: 3
  immich-machine-learning:
    image: ghcr.io/immich-app/immich-machine-learning:${IMMICH_VERSION:-release}-rknn
    devices:
      - /dev/rknpu:/dev/rknpu
    volumes:
      - model-cache:/cache
    security_opt:
      - systempaths=unconfined
    deploy:
      resources:
        limits:
          memory: 2g
  redis:
    image: valkey/valkey:8
    healthcheck:
      test: redis-cli ping || exit 1
  database:
    image: postgres:14
    environment:
      POSTGRES_PASSWORD: ${DB_PASSWORD}
      POSTGRES_INITDB_ARGS: '--data-checksums'
    shm_size: 128mb
  debug:
    image: busybox
    profiles: [debug]
volumes:
  model-cache:
"""

ENV = "IMMICH_VERSION=v2.1.0\nUPLOAD_LOCATION=./library\nDB_PASSWORD=secret\n"


def model():
    return load_model(
        "immich",
        "/srv/immich",
        {"/srv/immich/compose.yaml": COMPOSE},
        ENV,
        "/srv/immich/.env",
        read_file=lambda path: ENV if path == "/srv/immich/.env" else None,
    )


def test__parse_env_file():
    assert parse_env_file("A=1\n# c\nexport B='x y'\nC=\"z\" \nD=val # note\n") == {
        "A": "1",
        "B": "x y",
        "C": "z",
        "D": "val",
    }


def test__interpolate():
    env = {"A": "1", "E": ""}
    assert (
        interpolate("${A}-${B:-two}-${E:-empty}-${E-set}-$A-$$", env)
        == "1-two-empty--1-$"
    )
    with pytest.raises(ComposeError):
        interpolate("${MISSING:?needed}", env)


def test__load_model_should_skip_profiles_and_resolve_variables():
    m = model()
    assert set(m.services) == {
        "immich-server",
        "immich-machine-learning",
        "redis",
        "database",
    }
    assert (
        m.services["immich-server"]["image"]
        == "ghcr.io/immich-app/immich-server:v2.1.0"
    )
    assert m.services["immich-machine-learning"]["image"].endswith(":v2.1.0-rknn")


def test__load_model_should_reject_build_only_services():
    with pytest.raises(ComposeError):
        load_model(
            "x", "/x", {"/x/compose.yaml": "services:\n  a:\n    build: .\n"}, None
        )


def test__start_order_should_put_dependencies_first():
    order = start_order(model())
    assert order.index("redis") < order.index("immich-server")
    assert order.index("database") < order.index("immich-server")


def test__build_container_config_server():
    config = build_container_config(model(), "immich-server")
    host = config["HostConfig"]
    assert config["Image"] == "ghcr.io/immich-app/immich-server:v2.1.0"
    assert "/srv/immich/library:/data:rw" in host["Binds"]
    assert "/etc/localtime:/etc/localtime:ro" in host["Binds"]
    assert host["PortBindings"] == {"2283/tcp": [{"HostIp": "", "HostPort": "2283"}]}
    assert host["RestartPolicy"] == {"Name": "unless-stopped"}
    assert host["NetworkMode"] == "immich_default"
    assert "DB_PASSWORD=secret" in config["Env"]
    assert config["Healthcheck"]["Interval"] == 90 * 10**9
    labels = config["Labels"]
    assert labels["com.docker.compose.project"] == "immich"
    assert labels["com.docker.compose.service"] == "immich-server"
    assert labels["com.docker.compose.project.working_dir"] == "/srv/immich"
    assert LABEL_CONFIG_HASH in labels
    aliases = config["NetworkingConfig"]["EndpointsConfig"]["immich_default"]["Aliases"]
    assert aliases[0] == "immich-server"


def test__build_container_config_ml_devices_and_limits():
    config = build_container_config(model(), "immich-machine-learning")
    host = config["HostConfig"]
    assert host["Devices"] == [
        {
            "PathOnHost": "/dev/rknpu",
            "PathInContainer": "/dev/rknpu",
            "CgroupPermissions": "rwm",
        }
    ]
    assert "immich_model-cache:/cache:rw" in host["Binds"]
    assert host["Memory"] == 2 * 1024**3
    # CLI-only option: the docker CLI sends empty masked and read-only paths instead
    assert "SecurityOpt" not in host
    assert host["MaskedPaths"] == [] and host["ReadonlyPaths"] == []


def test__build_container_config_shell_healthcheck_and_shm():
    m = model()
    assert build_container_config(m, "redis")["Healthcheck"]["Test"] == [
        "CMD-SHELL",
        "redis-cli ping || exit 1",
    ]
    assert (
        build_container_config(m, "database")["HostConfig"]["ShmSize"] == 128 * 1024**2
    )


def test__config_hash_should_change_with_image():
    m = model()
    before = build_container_config(m, "immich-server")["Labels"][LABEL_CONFIG_HASH]
    m.services["immich-server"]["image"] = "ghcr.io/immich-app/immich-server:v2.2.0"
    after = build_container_config(m, "immich-server")["Labels"][LABEL_CONFIG_HASH]
    assert before != after


def test__parse_ports_variants():
    exposed, bindings = parse_ports(
        [
            "127.0.0.1:8080:80",
            "53:53/udp",
            "9000",
            {"target": 443, "published": "8443"},
            "7000-7001:7000-7001",
        ]
    )
    assert bindings["80/tcp"] == [{"HostIp": "127.0.0.1", "HostPort": "8080"}]
    assert bindings["53/udp"] == [{"HostIp": "", "HostPort": "53"}]
    assert bindings["9000/tcp"] == [{"HostIp": "", "HostPort": ""}]
    assert bindings["443/tcp"] == [{"HostIp": "", "HostPort": "8443"}]
    assert bindings["7001/tcp"] == [{"HostIp": "", "HostPort": "7001"}]


def test__network_mode_host_and_service():
    m = load_model(
        "cams",
        "/srv/cams",
        {
            "/srv/cams/compose.yaml": "services:\n  go2rtc:\n    image: go2rtc\n    network_mode: host\n"
            "  sidecar:\n    image: busybox\n    network_mode: service:go2rtc\n"
        },
        None,
    )
    assert build_container_config(m, "go2rtc")["HostConfig"]["NetworkMode"] == "host"
    config = build_container_config(m, "sidecar", containers={"go2rtc": "abc"})
    assert config["HostConfig"]["NetworkMode"] == "container:abc"
    assert "NetworkingConfig" not in config


def test__durations_and_sizes():
    assert duration_ns("1m30s") == 90 * 10**9
    assert duration_ns(5) == 5 * 10**9
    assert size_bytes("128mb") == 128 * 1024**2
    assert size_bytes("1g") == 1024**3


async def test__engine_up_should_create_network_volumes_and_containers_in_order():
    docker = FakeDocker()
    engine = ComposeEngine(docker)
    await engine.async_up(model())

    created = [c for c in docker.calls if c[0] == "create"]
    names = [c[1] for c in created]
    assert names.index("immich-redis-1") < names.index("immich_server")
    assert ("network_create", "immich_default") in docker.calls
    assert ("volume_create", "immich_model-cache") in docker.calls
    assert all(c["State"] == "running" for c in docker.containers.values())


async def test__engine_up_should_keep_unchanged_and_recreate_changed():
    docker = FakeDocker()
    engine = ComposeEngine(docker)
    m = model()
    await engine.async_up(m)
    docker.calls.clear()

    await engine.async_up(m)
    assert not [c for c in docker.calls if c[0] in ("create", "remove")]

    m.services["immich-server"]["image"] = "ghcr.io/immich-app/immich-server:v2.2.0"
    await engine.async_up(m)
    # the new container is created first, then the old one goes
    create = docker.calls.index(("create", "immich_server-docker-apps-new"))
    assert create < docker.calls.index(("remove", "immich_server"))
    assert ("rename", "immich_server") in docker.calls
    assert len([c for c in docker.calls if c[0] == "create"]) == 1
    names = [c["Names"][0] for c in docker.containers.values()]
    assert "/immich_server" in names and len(names) == 4


async def test__engine_up_should_keep_the_old_container_when_create_fails():
    docker = FakeDocker()
    engine = ComposeEngine(docker)
    m = model()
    await engine.async_up(m)
    old = next(
        c for c in docker.containers.values() if c["Names"] == ["/immich_server"]
    )

    m.services["immich-server"]["security_opt"] = ["label=invalid"]
    with pytest.raises(DockerError, match="invalid --security-opt"):
        await engine.async_up(m)
    assert docker.containers[old["Id"]]["State"] == "running"


async def test__engine_up_should_remove_orphans_and_down_should_clean_up():
    docker = FakeDocker()
    engine = ComposeEngine(docker)
    m = model()
    await engine.async_up(m)
    del m.services["database"]
    await engine.async_up(m)
    assert ("remove", "immich-database-1") in docker.calls

    await engine.async_down(m)
    assert not docker.containers
    assert "immich_default" not in docker.networks
