import pytest

from custom_components.docker_apps._compose_file import (
    parse_services,
    set_env_value,
    update_service_tag,
)

COMPOSE = """\
# Immich
name: immich
services:
  immich-server:
    container_name: immich_server
    # pinned on purpose
    image: ghcr.io/immich-app/immich-server:${IMMICH_VERSION:-release}
    ports:
      - "2283:2283"
  immich-machine-learning:
    image: "ghcr.io/immich-app/immich-machine-learning:v2.1.0-rknn"  # NPU build
    devices:
      - /dev/rknpu:/dev/rknpu
  redis:
    image: docker.io/valkey/valkey:8-bookworm@sha256:abc
  database:
    image: postgres:14
volumes:
  model-cache:
"""

ENV = "UPLOAD_LOCATION=/srv/photos\nIMMICH_VERSION=v2.1.0\n"


def files():
    return {"/opt/immich/compose.yaml": COMPOSE}


def test__parse_services():
    services = parse_services(COMPOSE)
    assert set(services) == {
        "immich-server",
        "immich-machine-learning",
        "redis",
        "database",
    }


def test__update_service_tag_should_edit_env_variable():
    edit = update_service_tag(
        files(), "/opt/immich/.env", ENV, "immich-server", "v2.2.0"
    )
    assert edit.content == COMPOSE
    assert edit.env_var == ("IMMICH_VERSION", "v2.2.0")
    assert "IMMICH_VERSION=v2.2.0\n" in edit.env_content
    assert "UPLOAD_LOCATION=/srv/photos\n" in edit.env_content


def test__update_service_tag_should_keep_quotes_and_comments():
    edit = update_service_tag(
        files(), None, None, "immich-machine-learning", "v2.2.0-rknn"
    )
    assert (
        'image: "ghcr.io/immich-app/immich-machine-learning:v2.2.0-rknn"  # NPU build'
        in edit.content
    )
    assert "# pinned on purpose" in edit.content
    assert edit.content.count("\n") == COMPOSE.count("\n")
    assert edit.env_content is None


def test__update_service_tag_should_drop_digest():
    edit = update_service_tag(files(), None, None, "redis", "9-bookworm")
    assert "image: docker.io/valkey/valkey:9-bookworm\n" in edit.content


def test__update_service_tag_should_use_last_file_defining_the_image():
    override = "services:\n  database:\n    image: postgres:16\n"
    all_files = {**files(), "/opt/immich/compose.override.yaml": override}
    edit = update_service_tag(all_files, None, None, "database", "17")
    assert edit.file == "/opt/immich/compose.override.yaml"
    assert "postgres:17" in edit.content


def test__update_service_tag_should_fail_for_unknown_service():
    with pytest.raises(ValueError):
        update_service_tag(files(), None, None, "nope", "1")


def test__set_env_value_should_append_missing_key():
    assert set_env_value("A=1", "B", "2") == "A=1\nB=2\n"


def test__set_env_value_should_keep_quotes():
    assert set_env_value('V="1"\n', "V", "2") == 'V="2"\n'
