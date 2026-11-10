# cspell:words Blkio Cgroupns INITDB hwaccel rkmpp

import pytest

from custom_components.docker_apps._compose import (
    LABEL_CONFIG_HASH,
    ComposeEngine,
    ComposeError,
    build_container_config,
    duration_ns,
    interpolate,
    load_model,
    merge,
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


HWACCEL = """
services:
  rkmpp:
    security_opt:
      - systempaths=unconfined
    group_add: [video]
    devices:
      - /dev/dri:/dev/dri
  cpu: {}
"""


def test__load_model_should_merge_extends_from_another_file():
    compose = """
services:
  server:
    image: ghcr.io/immich-app/immich-server:${VERSION}
    extends:
      file: hwaccel.yml
      service: rkmpp
    devices:
      - /dev/mali0:/dev/mali0
"""
    files = {"/srv/a/hwaccel.yml": HWACCEL}
    m = load_model(
        "a",
        "/srv/a",
        {"/srv/a/compose.yaml": compose},
        "VERSION=v3.3.5",
        read_file=files.get,
    )
    server = m.services["server"]
    assert "extends" not in server
    assert server["image"].endswith(":v3.3.5")
    assert server["group_add"] == ["video"]
    assert server["devices"] == ["/dev/dri:/dev/dri", "/dev/mali0:/dev/mali0"]
    host = build_container_config(m, "server")["HostConfig"]
    assert host["MaskedPaths"] == []
    assert host["GroupAdd"] == ["video"]


def test__load_model_should_merge_extends_from_same_file():
    compose = """
services:
  base:
    image: busybox
    environment: {A: "1"}
  app:
    extends: base
    environment: {B: "2"}
"""
    m = load_model("a", "/srv/a", {"/srv/a/compose.yaml": compose}, None)
    assert m.services["app"]["image"] == "busybox"
    assert m.services["app"]["environment"] == {"A": "1", "B": "2"}


@pytest.mark.parametrize(
    ("compose", "error"),
    [
        (
            "services:\n  a:\n    image: x\n    extends: {file: none.yml, service: b}\n",
            "not found",
        ),
        ("services:\n  a:\n    image: x\n    extends: b\n", "unknown service"),
        (
            "services:\n  a:\n    image: x\n    extends: b\n"
            "  b:\n    image: x\n    extends: a\n",
            "loops",
        ),
    ],
)
def test__load_model_should_reject_bad_extends(compose, error):
    with pytest.raises(ComposeError, match=error):
        load_model(
            "a", "/srv/a", {"/srv/a/compose.yaml": compose}, None, read_file={}.get
        )


def test__load_model_names_the_file_with_broken_yaml():
    with pytest.raises(ComposeError, match="compose.yaml is not valid YAML") as err:
        load_model(
            "a", "/srv/a", {"/srv/a/compose.yaml": "services:\n  a:\n   b: [x\n"}, None
        )
    assert "unicode string" not in str(err.value)


def test__start_order_should_put_dependencies_first():
    order = start_order(model())
    assert order.index("redis") < order.index("immich-server")
    assert order.index("database") < order.index("immich-server")


def test__volumes_from_should_name_containers_and_start_after_them():
    files = {
        "/srv/a/compose.yaml": """
services:
  app:
    image: busybox
    volumes_from: [data, "data2:ro", "container:outside:rw"]
  data:
    image: busybox
  data2:
    image: busybox
    container_name: second
  bad:
    image: busybox
    volumes_from: [missing]
"""
    }
    loaded = load_model("a", "/srv/a", files, None)
    order = start_order(loaded, ["app"])
    assert order.index("data") < order.index("app")
    assert order.index("data2") < order.index("app")
    config = build_container_config(loaded, "app", {"data": "abc"})
    assert config["HostConfig"]["VolumesFrom"] == ["abc", "second:ro", "outside:rw"]
    with pytest.raises(ComposeError, match="service 'missing' is not defined"):
        build_container_config(loaded, "bad")


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


def test__build_container_config_passes_device_rules_runtime_and_limits():
    compose = """
services:
  ha:
    image: homeassistant
    device_cgroup_rules: ["c 188:* rmw"]
    runtime: nvidia
    cgroup: host
    cgroup_parent: apps
    cpu_period: 100000
    cpu_quota: 50000
    cpu_rt_runtime: 10ms
    mem_swappiness: 0
    oom_kill_disable: true
    storage_opt: {size: 1G}
    dns_opt: [use-vc]
    uts: host
    blkio_config:
      weight: 300
      device_read_bps: [{path: /dev/sda, rate: 1mb}]
      device_write_iops: [{path: /dev/sda, rate: 100}]
    platform: linux/amd64
    x-note: kept out
"""
    m = load_model("ha", "/srv/ha", {"/srv/ha/compose.yaml": compose}, None)
    host = build_container_config(m, "ha")["HostConfig"]
    assert host["DeviceCgroupRules"] == ["c 188:* rmw"]
    assert host["Runtime"] == "nvidia"
    assert host["CgroupnsMode"] == "host"
    assert host["CgroupParent"] == "apps"
    assert (host["CpuPeriod"], host["CpuQuota"]) == (100000, 50000)
    assert host["CpuRealtimeRuntime"] == 10000
    assert host["MemorySwappiness"] == 0
    assert host["OomKillDisable"] is True
    assert host["StorageOpt"] == {"size": "1G"}
    assert host["DnsOptions"] == ["use-vc"]
    assert host["UTSMode"] == "host"
    assert host["BlkioWeight"] == 300
    assert host["BlkioDeviceReadBps"] == [{"Path": "/dev/sda", "Rate": 1024**2}]
    assert host["BlkioDeviceWriteIOps"] == [{"Path": "/dev/sda", "Rate": 100}]


def test__new_keys_do_not_change_the_hash_of_apps_without_them():
    # an app without them must not be recreated after this version is installed
    host = build_container_config(model(), "redis")["HostConfig"]
    assert not {"Runtime", "OomKillDisable", "DeviceCgroupRules"} & host.keys()


def test__secrets_and_configs_from_files_are_mounted_read_only():
    compose = """
services:
  app:
    image: app
    secrets: [db_password, {source: api_key, target: key.txt}]
    configs: [{source: app_conf, target: /etc/app.conf}, plain]
secrets:
  db_password: {file: ./secrets/db.txt}
  api_key: {file: /opt/key}
configs:
  app_conf: {file: app.conf}
  plain: {file: plain.conf}
"""
    m = load_model("app", "/srv/app", {"/srv/app/compose.yaml": compose}, None)
    mounts = build_container_config(m, "app")["HostConfig"]["Mounts"]
    assert [(x["Source"], x["Target"], x["ReadOnly"]) for x in mounts] == [
        ("/srv/app/secrets/db.txt", "/run/secrets/db_password", True),
        ("/opt/key", "/run/secrets/key.txt", True),
        ("/srv/app/app.conf", "/etc/app.conf", True),
        ("/srv/app/plain.conf", "/plain", True),
    ]


@pytest.mark.parametrize(
    ("extra", "error"),
    [
        ({"post_start": [{"command": "echo"}]}, "post_start is not supported"),
        ({"links": ["db"], "scale": 2}, "links, scale are not supported"),
        ({"secrets": ["missing"]}, "secret 'missing' is not defined"),
        ({"configs": ["inline"]}, "config 'inline': only 'file' is supported"),
    ],
)
async def test__unsupported_keys_are_refused_and_the_app_left_as_it_is(extra, error):
    docker = FakeDocker()
    engine = ComposeEngine(docker)
    m = model()
    await engine.async_up(m)
    before = set(docker.containers)
    docker.calls.clear()

    m.configs["inline"] = {"content": "x"}
    m.services["redis"].update(extra)
    with pytest.raises(ComposeError, match=error):
        await engine.async_up(m)
    assert not docker.calls  # nothing created, removed or changed
    assert set(docker.containers) == before


async def test__engine_up_should_remove_orphans_and_down_should_clean_up():
    docker = FakeDocker()
    engine = ComposeEngine(docker)
    m = model()
    await engine.async_up(m)
    del m.services["database"]
    await engine.async_up(m)
    assert ("remove", "immich-database-1") in docker.calls

    await engine.async_down(m.project)
    assert not docker.containers
    assert "immich_default" not in docker.networks


async def test__engine_up_should_keep_containers_of_profile_services():
    docker = FakeDocker()
    engine = ComposeEngine(docker)
    m = model()
    assert m.inactive == {"debug"}
    # as `docker compose --profile debug up -d` leaves it
    started = model()
    started.services["debug"] = {"image": "busybox"}
    await engine.async_up(started)
    await engine.async_up(m, force_recreate=True)
    assert ("remove", "immich-debug-1") not in docker.calls
    assert "immich-debug-1" in {c["Names"][0][1:] for c in docker.containers.values()}


async def test__recreate_keeps_anonymous_volumes_and_labels_new_ones():
    docker = FakeDocker()
    engine = ComposeEngine(docker)
    m = load_model(
        "pg",
        "/srv/pg",
        {
            "/srv/pg/compose.yaml": "services:\n  db:\n    image: postgres:16\n"
            "    volumes:\n      - /cache\n"
        },
        None,
    )
    docker.add_image("postgres:16")
    # the image declares its own volume, which the compose file doesn't name
    docker.images["postgres:16"]["Config"] = {
        "Volumes": {"/var/lib/postgresql/data": {}}
    }

    await engine.async_up(m)
    first = next(iter(docker.containers.values()))
    mounts = first["HostConfig"]["Mounts"]
    assert {mount["Target"] for mount in mounts} == {
        "/cache",
        "/var/lib/postgresql/data",
    }
    labels = {"com.docker.compose.project": "pg", "com.docker.compose.service": "db"}
    assert all(mount["VolumeOptions"]["Labels"] == labels for mount in mounts)
    names = {mount["Destination"]: mount["Name"] for mount in first["Mounts"]}

    await engine.async_up(m, force_recreate=True)
    second = next(iter(docker.containers.values()))
    assert second["Id"] != first["Id"]
    # the same volumes again: their data stays
    reused = {
        mount["Target"]: mount["Source"] for mount in second["HostConfig"]["Mounts"]
    }
    assert reused == names


async def test__down_with_volumes_keeps_a_volume_another_container_uses():
    docker = FakeDocker()
    engine = ComposeEngine(docker)
    label = {"com.docker.compose.project": "immich"}
    docker.volumes = {
        "immich_db": {"Name": "immich_db", "Labels": label},
        "immich_shared": {"Name": "immich_shared", "Labels": label, "InUse": True},
        "other_db": {"Name": "other_db", "Labels": {}},
    }
    await engine.async_down("immich", volumes=True)
    assert sorted(docker.volumes) == ["immich_shared", "other_db"]


def test__environment_list_and_map_merge_by_key():
    base = {"services": {"app": {"environment": ["A=1", "B=2"]}}}
    override = {"services": {"app": {"environment": {"B": "3", "C": "4"}}}}
    merged = merge(base, override)["services"]["app"]["environment"]
    assert merged == {"A": "1", "B": "3", "C": "4"}


def test__override_and_reset_tags_replace_instead_of_merge():
    base = "services:\n  app:\n    image: a\n    volumes:\n      - ./data:/data\n      - /x:/x\n    ports:\n      - 80:80\n"
    override = "services:\n  app:\n    volumes: !override\n      - /new:/new\n    ports: !reset\n"
    m = load_model(
        "p",
        "/srv/p",
        {"/srv/p/compose.yaml": base, "/srv/p/compose.override.yaml": override},
        None,
    )
    assert m.services["app"]["volumes"] == ["/new:/new"]
    assert "ports" not in m.services["app"]


def test__interpolate_checks_the_variable_before_the_operator():
    with pytest.raises(ComposeError, match="PW is required"):
        interpolate("${PW:?must-be-set}", {})
    assert interpolate("${X:+--flag}", {"X": "1"}) == "--flag"
    assert interpolate("${X:+--flag}", {}) == ""
    assert interpolate("${A-b-c}", {}) == "b-c"
