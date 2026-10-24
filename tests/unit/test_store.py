# cspell:words appstore icewhale riscv

import io
import json
import tarfile

import yaml

from custom_components.docker_apps._store import (
    KIND_CASAOS,
    KIND_RUNTIPI,
    StoreApp,
    archive_url,
    format_env,
    parse_archive,
    parse_casaos_compose,
    parse_runtipi,
    published_ports,
    render_app,
    runtipi_dynamic_to_compose,
)

CASAOS = """
name: jellyfin
services:
  jellyfin:
    image: linuxserver/jellyfin:12.2ubu2604-ls53
    environment:
      PUID: $PUID
      TZ: $TZ
    ports:
      - target: 8096
        published: "8097"
        protocol: tcp
    volumes:
      - type: bind
        source: /DATA/AppData/$AppID/config
        target: /config
      - type: bind
        source: /DATA/Media
        target: /Media
    x-casaos:
      envs: []
x-casaos:
  id: org.icewhale.jellyfin
  architectures: [amd64, arm64]
  main: jellyfin
  category: Media
  title:
    en_US: Jellyfin
  tagline:
    en_US: The personal Media System
  port_map: "8097"
  index: /
  version: "12.2.0"
  release_notes:
    en_US: Bug fixes
"""

TIPI_CONFIG = json.dumps(
    {
        "name": "Immich",
        "id": "immich",
        "available": True,
        "port": 8128,
        "tipi_version": 240,
        "version": "v3.3.5",
        "categories": ["photography"],
        "short_desc": "Photos",
        "form_fields": [
            {
                "type": "random",
                "label": "DB_PASSWORD",
                "min": 32,
                "env_variable": "DB_PASSWORD",
            },
            {
                "type": "text",
                "label": "Name",
                "env_variable": "NAME",
                "default": "home",
            },
        ],
        "supported_architectures": ["arm64", "amd64"],
    }
)
TIPI_COMPOSE = """
version: '3.7'
services:
  immich:
    image: ghcr.io/immich-app/immich-server:v3.3.5
    ports:
      - ${APP_PORT}:2283
    volumes:
      - ${APP_DATA_DIR}/data/upload:/data
    networks:
      - tipi_main_network
    labels:
      traefik.enable: true
      runtipi.managed: true
networks:
  tipi_main_network:
    name: runtipi_tipi_main_network
    external: true
"""


def test__parse_casaos_compose():
    app = parse_casaos_compose(CASAOS, "repo")
    assert app.id == "repo.org-icewhale-jellyfin"
    assert app.name == "Jellyfin"
    assert app.version == "12.2.0"
    assert app.web_port == 8097
    assert app.supports("aarch64")
    assert not app.supports("riscv64")
    assert app.release_notes == "Bug fixes"


def test__render_casaos_maps_data_folders_and_drops_metadata():
    app = parse_casaos_compose(CASAOS, "repo")
    compose, env = render_app(
        app,
        "jellyfin",
        "/data/jellyfin",
        "/data",
        {},
        {"TZ": "UTC", "PUID": "1000", "PGID": "1000"},
    )
    data = yaml.safe_load(compose)
    assert "x-casaos" not in data
    assert "x-casaos" not in data["services"]["jellyfin"]
    sources = [v["source"] for v in data["services"]["jellyfin"]["volumes"]]
    assert sources == ["/data/jellyfin/config", "/data/Media"]
    assert env["AppID"] == "jellyfin"
    assert published_ports(compose) == {8097}


def test__parse_runtipi_and_render():
    app = parse_runtipi(TIPI_CONFIG, TIPI_COMPOSE, None, "tipi")
    assert app.package_version == "240"
    assert app.is_newer_than("239", None)
    assert not app.is_newer_than("240", None)
    compose, env = render_app(app, "immich", "/data/immich", "/data", {}, {"TZ": "UTC"})
    data = yaml.safe_load(compose)
    assert "networks" not in data
    assert "networks" not in data["services"]["immich"]
    assert "labels" not in data["services"]["immich"]
    assert env["APP_DATA_DIR"] == "/data/immich"
    assert env["APP_PORT"] == "8128"
    assert len(env["DB_PASSWORD"]) >= 32
    assert env["NAME"] == "home"


def test__render_keeps_stored_secrets():
    app = parse_runtipi(TIPI_CONFIG, TIPI_COMPOSE, None, "tipi")
    _, env = render_app(app, "immich", "/d", "/", {"DB_PASSWORD": "kept"}, {})
    assert env["DB_PASSWORD"] == "kept"


def test__runtipi_dynamic_compose():
    data = runtipi_dynamic_to_compose(
        {
            "services": [
                {
                    "name": "jellyfin",
                    "image": "lscr.io/linuxserver/jellyfin:1",
                    "isMain": True,
                    "internalPort": 8096,
                    "environment": [{"key": "TZ", "value": "${TZ}"}],
                    "volumes": [
                        {
                            "hostPath": "${APP_DATA_DIR}/config",
                            "containerPath": "/config",
                        }
                    ],
                }
            ]
        }
    )
    service = data["services"]["jellyfin"]
    assert service["ports"] == ["${APP_PORT}:8096"]
    assert service["environment"] == {"TZ": "${TZ}"}
    assert service["volumes"] == ["${APP_DATA_DIR}/config:/config"]


def _tar(files: dict[str, str]) -> bytes:
    buffer = io.BytesIO()
    with tarfile.open(fileobj=buffer, mode="w:gz") as tar:
        for name, content in files.items():
            raw = content.encode()
            info = tarfile.TarInfo(name)
            info.size = len(raw)
            tar.addfile(info, io.BytesIO(raw))
    return buffer.getvalue()


def test__parse_archive_casaos_and_runtipi():
    casaos = _tar(
        {
            "store-main/Apps/Jellyfin/docker-compose.yml": CASAOS,
            "store-main/Apps/Jellyfin/icon.png": "x",
        }
    )
    assert [a.name for a in parse_archive(casaos, KIND_CASAOS, "r")] == ["Jellyfin"]

    tipi = _tar(
        {
            "appstore-master/apps/immich/config.json": TIPI_CONFIG,
            "appstore-master/apps/immich/docker-compose.yml": TIPI_COMPOSE,
            "appstore-master/apps/immich/metadata/description.md": "# Immich",
        }
    )
    apps = parse_archive(tipi, KIND_RUNTIPI, "r", "https://raw.example/o/r/HEAD")
    assert apps[0].name == "Immich"
    assert apps[0].description == "# Immich"
    assert apps[0].icon == "https://raw.example/o/r/HEAD/apps/immich/metadata/logo.jpg"


def test__store_app_roundtrip():
    app = parse_runtipi(TIPI_CONFIG, TIPI_COMPOSE, None, "tipi")
    assert StoreApp.from_dict(app.as_dict()) == app


def test__archive_url_and_env_format():
    assert archive_url("https://github.com/runtipi/runtipi-appstore") == (
        "https://codeload.github.com/runtipi/runtipi-appstore/tar.gz/HEAD"
    )
    assert format_env({"A": "1", "B": "x y"}).splitlines()[1:] == ["A=1", "B='x y'"]


def test__render_casaos_moves_the_web_port():
    app = parse_casaos_compose(CASAOS, "repo")
    compose, _ = render_app(app, "jellyfin", "/d/j", "/d", {"APP_PORT": "18090"}, {})
    assert published_ports(compose) == {18090}


def test__casaos_without_version_uses_the_image_tag():
    text = CASAOS.replace('  version: "12.2.0"\n', "").replace(
        "linuxserver/jellyfin:12.2ubu2604-ls53",
        "linuxserver/jellyfin:12.2ubu2604-ls53@sha256:abc",
    )
    app = parse_casaos_compose(text, "repo")
    assert app.version == "12.2ubu2604-ls53"
    assert app.package_version.startswith(
        "sha256:"
    )  # hash still detects package changes


VSCODE = """
services:
  app:
    image: linuxserver/vscode:1.142.0
    ports:
      - target: 3000
        published: 3000
      - target: 3001
        published: 3001
x-casaos:
  main: app
  port_map: "3000"
  title:
    en_us: Vscode
"""


def test__desktop_images_use_https():
    app = parse_casaos_compose(VSCODE, "bb")
    assert (app.web_port, app.web_target, app.web_scheme) == (3001, 3001, "https")
    compose, _ = render_app(app, "vscode", "/data/vscode", "/data", {}, {})
    ports = yaml.safe_load(compose)["services"]["app"]["ports"]
    assert [p["target"] for p in ports] == [3001]  # the HTTP port isn't published


def test__store_scheme_is_used():
    text = CASAOS.replace("\nx-casaos:\n", "\nx-casaos:\n  scheme: https\n", 1)
    app = parse_casaos_compose(text, "repo")
    assert app.web_scheme == "https"
    assert parse_casaos_compose(CASAOS, "repo").web_scheme == "http"
