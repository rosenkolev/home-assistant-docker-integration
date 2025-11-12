import pytest

from custom_components.home_assistant_docker_integration.binary_sensor import DockerImageSensor

class MockedDataUpdateCoordinator:

def test__GpioBinarySensor_should_init_default_state(mocked_factory):
    coordinator = MockedDataUpdateCoordinator()
    sensor = DockerImageSensor(coordinator, "test_id")
    sensor.
