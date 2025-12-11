from unittest.mock import Mock

import pytest

from custom_components.home_assistant_docker_integration._docker_api import DockerApi
from custom_components.home_assistant_docker_integration.switch import (
    DockerContainerSwitch,
)
from tests.mocks import MockedDataUpdateCoordinator, create_mocked_container


def test__DockerContainerSwitch_should_init_default_state():
    item = create_mocked_container()
    coordinator = MockedDataUpdateCoordinator("789")
    coordinator.add_container(item)
    sensor = DockerContainerSwitch(coordinator, item.short_id)
    assert sensor.is_on is True


@pytest.mark.asyncio
async def test__DockerContainerSwitch_should_turn_off():
    item = create_mocked_container()

    container = Mock()

    def _mock_get_fn(id):
        if id == item.id:
            return container

    api = DockerApi()
    api.client = Mock()
    api.client.containers.get = Mock(side_effect=_mock_get_fn)

    coordinator = MockedDataUpdateCoordinator("100")
    coordinator.api = api
    coordinator.add_container(item)
    sensor = DockerContainerSwitch(coordinator, item.short_id)

    await sensor.async_turn_off()

    assert container.stop.called
