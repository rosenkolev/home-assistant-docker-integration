import docker
import asyncio

from .const import _LOGGER

class DockerHub:
  def __init__(self, entity_id: str):
    self.name = "Docker"
    self.entity_id = entity_id
    self.firmware_version = "1.0"
    self.hardware_version = "1.0"
    _LOGGER.info("before docker client")
    self.client = docker.DockerClient(base_url='unix://var/run/docker.sock')
    _LOGGER.info("after docker client")

  def auto_connect(self):
    def blocking_code(client):
      _LOGGER.info(client.version())
      _LOGGER.info(client.info())

    loop = asyncio.get_running_loop()
    loop.run_in_executor(None, blocking_code, self.client)

  def close(self):
    if self.client is not None:
        self.client.close()
        self.client = None
