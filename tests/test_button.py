from unittest.mock import Mock

import pytest

from custom_components.home_assistant_docker_integration._docker_api import DockerApi
from custom_components.home_assistant_docker_integration.button import (
    DockerContainerRestartButton,
)
from tests.mocks import MockedDataUpdateCoordinator, create_mocked_container


@pytest.mark.asyncio
async def test__DockerContainerRestartButton_should_restart():
    item = create_mocked_container()

    container = Mock()

    def _mock_get_fn(id):
        if id == item.id:
            return container

    api = DockerApi()
    api.client = Mock()
    api.client.containers.get = Mock(side_effect=_mock_get_fn)

    coordinator = MockedDataUpdateCoordinator("200")
    coordinator.api = api
    coordinator.add_container(item)
    sensor = DockerContainerRestartButton(coordinator, item.short_id)

    await sensor.async_press()

    assert container.restart.called
