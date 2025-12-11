from custom_components.home_assistant_docker_integration.binary_sensor import (
    DockerImageSensor,
    DockerVolumeSensor,
)
from tests.mocks import MOCKED_IMAGE, MOCKED_VOLUME, MockedDataUpdateCoordinator


def test__DockerImageSensor_should_init_default_state():
    coordinator = MockedDataUpdateCoordinator("123")
    coordinator.add_image(MOCKED_IMAGE)
    sensor = DockerImageSensor(coordinator, "502bc8dd565a")
    assert sensor.is_on is True


def test__DockerVolumeSensor_should_init_default_state():
    coordinator = MockedDataUpdateCoordinator("456")
    coordinator.add_volume(MOCKED_VOLUME)
    sensor = DockerVolumeSensor(coordinator, "fae919bd0d88c1809b8f3472e6")
    assert sensor.is_on is True
