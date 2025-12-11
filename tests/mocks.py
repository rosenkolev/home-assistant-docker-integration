from dataclasses import dataclass

from custom_components.home_assistant_docker_integration._docker_api import (
    DockerContainerInfo,
    DockerHostInfo,
    DockerImageInfo,
    DockerVolumeInfo,
)


@dataclass()
class MockedConfigEntry:
    entry_id: str


class MockedDataUpdateCoordinator:
    def __init__(self, entry_id: str):
        self.config_entry = MockedConfigEntry(entry_id)

    data = DockerHostInfo(
        version="20",
        containers_total=2,
        containers_running=1,
        images_total=3,
        firewall="",
        containers=dict(),
        images=dict(),
        volumes=dict(),
    )

    def add_container(self, item: DockerContainerInfo):
        self.data.containers[item.short_id] = item

    def add_image(self, item: DockerImageInfo):
        self.data.images[item.id[:12]] = item

    def add_volume(self, item: DockerVolumeInfo):
        self.data.volumes[item.name[:26]] = item


def create_mocked_container(
    id="ab1cd2ef3gh4ij5kl6mn7",
    short_id="ab1cd2ef3gh4",
    name="Traefik",
    state="running",
    status="healthy",
    image_id="qw11er22ty33ui44",
    image_name="traefik/traefik:latest",
    compose_project=None,
    ports=[],
    mounts=[],
):
    return DockerContainerInfo(
        id=id,
        name=name,
        state=state,
        status=status,
        image_id=image_id,
        image_name=image_name,
        compose_project=compose_project,
        short_id=short_id,
        ports=ports,
        mounts=mounts,
    )


MOCKED_CONTAINER = create_mocked_container()
MOCKED_IMAGE = DockerImageInfo(
    id="502bc8dd565a23955c8a372b250a2f659397b39b205d4820bd521776c300ea76",
    title="Traefik",
    tag="traefik/traefik:latest",
    in_use=True,
    description="A modern reverse-proxy",
    rev="1",
)
MOCKED_VOLUME = DockerVolumeInfo(
    name="fae919bd0d88c1809b8f3472e6335dfe13fe1749885db6559e50d0409142fd6c",
    in_use=True,
    size="15KB",
    mount_point="/var/lib/docker/volumes/arcane_arcane-data/_data",
)
