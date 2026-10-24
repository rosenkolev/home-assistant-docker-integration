# cspell:words pgvectors pihole

import struct

from custom_components.docker_apps._docker_client import (
    _clean_params,
    build_docker_data,
    container_key,
    demux_logs,
    parse_disk_usage,
    parse_stats,
    parse_time,
)
from custom_components.docker_apps._models import split_image
from custom_components.docker_apps._paths import PathMap
from custom_components.docker_apps._registry import parse_reference


def raw(
    name, project=None, service=None, state="running", status="Up 2 hours (healthy)"
):
    labels = {}
    if project:
        labels = {
            "com.docker.compose.project": project,
            "com.docker.compose.service": service,
            "com.docker.compose.container-number": "1",
            "com.docker.compose.project.working_dir": f"/srv/{project}",
            "com.docker.compose.project.config_files": f"/srv/{project}/compose.yaml",
        }
    return {
        "Id": name * 8,
        "Names": [f"/{name}"],
        "Image": "img:1",
        "ImageID": "sha256:abc",
        "Labels": labels,
        "State": state,
        "Status": status,
        "Ports": [
            {"PrivatePort": 80, "PublicPort": 8080, "Type": "tcp"},
            {"PrivatePort": 443, "Type": "tcp"},
        ],
    }


def test__build_docker_data_groups_by_project():
    data = build_docker_data(
        {"ServerVersion": "27", "Containers": 3, "ContainersRunning": 2},
        [
            raw("immich_server", "immich", "immich-server"),
            raw("immich_redis", "immich", "redis", status="Up (unhealthy)"),
            raw("pihole"),
            {
                **raw("helper"),
                "Labels": {"io.github.rosenkolev.docker_apps.helper": "1"},
            },
        ],
    )
    assert set(data.apps) == {"immich", "pihole"}
    assert data.apps["immich"].container_keys == [
        "immich/immich-server",
        "immich/redis",
    ]
    assert data.apps["immich"].config_files == ["/srv/immich/compose.yaml"]
    assert data.containers["immich/immich-server"].health == "healthy"
    assert data.containers["immich/redis"].health == "unhealthy"
    assert data.containers["pihole"].ports == ["8080:80/tcp"]
    assert not data.apps["pihole"].is_compose


def test__container_key_numbers():
    labels = {
        "com.docker.compose.project": "p",
        "com.docker.compose.service": "s",
        "com.docker.compose.container-number": "2",
    }
    assert container_key(labels, "x") == ("p/s-2", "p")
    assert container_key({}, "x") == ("x", "x")


def test__parse_stats_cgroup_v2():
    stats = parse_stats(
        {
            "cpu_stats": {
                "cpu_usage": {"total_usage": 2000},
                "system_cpu_usage": 20000,
                "online_cpus": 4,
            },
            "precpu_stats": {
                "cpu_usage": {"total_usage": 1000},
                "system_cpu_usage": 10000,
            },
            "memory_stats": {
                "usage": 500,
                "limit": 1000,
                "stats": {"inactive_file": 100},
            },
        }
    )
    assert stats.cpu_percent == 40.0
    assert stats.memory_usage == 400


def test__parse_stats_without_previous_sample():
    stats = parse_stats({"cpu_stats": {}, "precpu_stats": {}, "memory_stats": {}})
    assert stats.cpu_percent is None
    assert stats.memory_usage is None


def test__demux_logs():
    def frame(stream, text):
        return bytes([stream, 0, 0, 0]) + struct.pack(">I", len(text)) + text

    assert demux_logs(frame(1, b"out\n") + frame(2, b"err\n")) == "out\nerr\n"
    assert demux_logs(b"plain text\n") == "plain text\n"


def test__parse_time():
    parsed = parse_time("2026-10-23T10:00:00.123456789Z")
    assert parsed.microsecond == 123456
    assert parsed.utcoffset().total_seconds() == 0
    assert parse_time("0001-01-01T00:00:00Z") is None
    assert parse_time(None) is None


def test__parse_disk_usage():
    disk = parse_disk_usage(
        {
            "LayersSize": 300,
            "Images": [{"Size": 100, "Containers": 0}, {"Size": 200, "Containers": 1}],
            "Volumes": [
                {"UsageData": {"Size": 50, "RefCount": 0}},
                {"UsageData": {"Size": -1, "RefCount": 1}},
            ],
        }
    )
    assert disk.images_reclaimable == 100
    assert disk.volumes_reclaimable == 50
    assert disk.volumes_unused == 1


def test__clean_params():
    assert _clean_params({"all": True, "x": None, "n": 5}) == {"all": "1", "n": "5"}


def test__split_image_and_reference():
    assert split_image("ghcr.io/a/b:v1@sha256:x") == ("ghcr.io/a/b", "v1", "sha256:x")
    assert split_image("localhost:5000/b") == ("localhost:5000/b", None, None)
    assert parse_reference("postgres:16") == (
        "registry-1.docker.io",
        "library/postgres",
        "16",
    )
    assert parse_reference("ghcr.io/immich-app/immich-server") == (
        "ghcr.io",
        "immich-app/immich-server",
        "latest",
    )
    assert parse_reference("docker.io/valkey/valkey:8") == (
        "registry-1.docker.io",
        "valkey/valkey",
        "8",
    )
    # pinned with tag and digest (Immich postgres)
    assert parse_reference(
        "ghcr.io/immich-app/postgres:14-vectorchord0.4.3-pgvectors0.2.0@sha256:abc"
    ) == ("ghcr.io", "immich-app/postgres", "sha256:abc")
    assert parse_reference("localhost:5000/b:1") == ("localhost:5000", "b", "1")


def test__path_map():
    paths = PathMap.from_inspect(
        {
            "Mounts": [
                {
                    "Type": "bind",
                    "Source": "/srv/apps",
                    "Destination": "/projects",
                },
                {
                    "Type": "bind",
                    "Source": "/srv/apps/immich",
                    "Destination": "/immich",
                },
                {"Type": "volume", "Source": "/var/lib/docker/x", "Destination": "/v"},
            ]
        }
    )
    assert (
        paths.to_local("/srv/apps/jellyfin/compose.yaml")
        == "/projects/jellyfin/compose.yaml"
    )
    assert paths.to_local("/srv/apps/immich/.env") == "/immich/.env"
    assert paths.to_local("/opt/other") is None
    assert PathMap(None).to_local("/x") == "/x"
