import asyncio
import docker
from datetime import timedelta

from homeassistant.core import HomeAssistant
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator
from homeassistant.exceptions import ConfigEntryNotReady

from .const import DOMAIN, _LOGGER
from .entity import DockerHostConfigEntry

class DockerDataUpdateCoordinator(DataUpdateCoordinator):
    """Data update coordinator for integration"""

    def __init__(self, hass: HomeAssistant, config_entry: DockerHostConfigEntry) -> None:
        super().__init__(hass, _LOGGER, name=DOMAIN, update_method=self._update_local, update_interval=timedelta(seconds=2))
        self.entity_id = config_entry.entry_id;
        self.data = {}
        self.client = None
        self.loop = asyncio.get_running_loop()

    async def initialize(self):
        """Initialize the integration"""
        await self.loop.run_in_executor(None, self._run_docker_info)

    async def _update_local(self):
        _LOGGER.debug("coordinator - start update_local")
        #await self._api.poll_refresh()
        _LOGGER.debug("coordinator - complete update_local")
        return self.data

    async def disconnect(self):
        """disconnect from api"""
        if self.client is not None:
            self.client.close()
            self.client = None

        await self.async_shutdown()

    def _run_docker_info(self) -> None:
        self.client = docker.DockerClient(base_url='unix://var/run/docker.sock')
        _LOGGER.warning(self.client.version())
        _LOGGER.warning(self.client.info())
