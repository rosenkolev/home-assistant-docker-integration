from dataclasses import dataclass
from typing import Any

import pytest

from custom_components.home_assistant_docker_integration._docker_api import DockerApi
from custom_components.home_assistant_docker_integration.coordinator import (
    ServiceController,
)


@dataclass()
class MockedConfigEntry:
    entry_id: str
    runtime_data: Any


@pytest.mark.asyncio
async def test__ServiceController_should_init():
    data = MockedConfigEntry("19", None)
    ctl = ServiceController(None, data)

    data.runtime_data = ctl

    assert ctl.data_coordinator.config_entry == data
    assert ctl.update_coordinator.config_entry == data
    assert isinstance(ctl.data_coordinator.api, DockerApi) is True
