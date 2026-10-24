from __future__ import annotations

import logging

DOMAIN = "docker_apps"
DEFAULT_NAME = "Docker Apps"
FRONTEND_URL = f"/{DOMAIN}_static"
PANEL_URL = "docker-apps"

CONF_DOCKER_URL = "docker_url"
CONF_APPS_ROOT = "apps_root"
CONF_DATA_ROOT = "data_root"
CONF_READ_ONLY = "read_only"
CONF_UPDATE_HOURS = "update_hours"
CONF_UNTRACKED = "untracked_images"
CONF_REPO_URL = "url"
CONF_REPO_KIND = "kind"

DEFAULT_DOCKER_URL = "unix:///var/run/docker.sock"
DEFAULT_APPS_ROOT = "/opt/docker-apps"
DEFAULT_DATA_ROOT = "/opt/docker-apps-data"
DEFAULT_UPDATE_HOURS = 6

SUBENTRY_REPOSITORY = "repository"
EVENT_DOCKER = f"{DOMAIN}_event"

KNOWN_REPOSITORIES = {
    "https://github.com/IceWhaleTech/CasaOS-AppStore": ("casaos", "CasaOS official"),
    "https://github.com/bigbeartechworld/big-bear-casaos": ("casaos", "BigBear CasaOS"),
    "https://github.com/runtipi/runtipi-appstore": ("runtipi", "Runtipi official"),
}

_LOGGER = logging.getLogger(__package__)
