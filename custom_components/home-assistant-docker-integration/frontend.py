from pathlib import Path

from homeassistant.components.frontend import (
    add_extra_js_url,
    async_register_built_in_panel,
    remove_extra_js_url,
)
from homeassistant.components.http import StaticPathConfig
from homeassistant.components.lovelace.const import (
    CONF_ICON,
    CONF_REQUIRE_ADMIN,
    CONF_TITLE,
    CONF_URL_PATH,
    LOVELACE_DATA,
    MODE_YAML,
)
from homeassistant.components.lovelace.dashboard import LovelaceYAML
from homeassistant.components.lovelace.resources import ResourceStorageCollection
from homeassistant.const import CONF_FILENAME
from homeassistant.core import HomeAssistant

from .const import _LOGGER, DOMAIN


async def async_register_frontend(hass: HomeAssistant):
    url = await async_register_static_path(hass, "www/docker_dashboard.js")
    await async_add_or_update_resource(hass, url, "0.8")

    if DOMAIN not in hass.data.get("frontend_panels", {}):
        add_dashboard_yaml_config(
            hass,
            "dashboard-docker",
            "docker_dashboard.yaml",
            "Docker",
            "mdi:docker",
            True,
        )


def add_dashboard_yaml_config(
    hass: HomeAssistant,
    url: str,
    config_file: str,
    title: str,
    icon: str,
    require_admin: bool,
):
    existing = hass.data[LOVELACE_DATA].dashboards.get(url)
    if not existing:
        hass.data[LOVELACE_DATA].dashboards[url] = LovelaceYAML(
            hass,
            url,
            {
                CONF_URL_PATH: url,
                CONF_TITLE: title,
                CONF_REQUIRE_ADMIN: require_admin,
                CONF_ICON: icon,
                CONF_FILENAME: f"custom_components/{DOMAIN}/{config_file}",
            },
        )
        async_register_built_in_panel(
            hass,
            component_name="lovelace",
            sidebar_title=title,
            sidebar_icon=icon,
            frontend_url_path=url,
            config={"mode": MODE_YAML},
            require_admin=require_admin,
            config_panel_domain=DOMAIN,
        )


async def async_register_static_path(hass: HomeAssistant, path: str):
    abs_path = Path(__file__).parent / path
    url = f"/hacsfiles/{DOMAIN}/{path}"

    await hass.http.async_register_static_paths([StaticPathConfig(url, abs_path, True)])

    return url


async def async_add_or_update_resource(hass: HomeAssistant, url: str, ver: str) -> bool:
    """Add extra JS module for lovelace mode YAML and new lovelace resource"""
    url2 = f"{url}?v={ver}"
    (item, resources, is_store) = await find_lovelace_resource(hass, url)
    if item is None:
        if is_store:
            _LOGGER.debug(f"Add new lovelace resource: {url2}")
            await resources.async_create_item({"res_type": "module", "url": url2})
        else:
            _LOGGER.debug(f"Add extra JS module: {url2}")
            add_extra_js_url(hass, url2)
    else:
        if item["url"].endswith(f"?v={ver}"):
            _LOGGER.debug(f"JS module is loaded: {url2}")
            return False

        if is_store:
            _LOGGER.debug(f"Update lovelace resource: {url2}")
            await resources.async_update_item(
                item["id"], {"res_type": "module", "url": url2}
            )
        else:
            item["url"] = url2

    return True


async def async_remove_resource(hass: HomeAssistant, url: str, ver: str):
    (item, resources, is_store) = await find_lovelace_resource(hass, url)
    if is_store:
        if item is not None:
            await resources.async_delete_item(item["url"])
    else:
        remove_extra_js_url(f"{url}?v={ver}")


async def find_lovelace_resource(hass: HomeAssistant, url: str):
    """find a lovelace dashboard resource (js module)"""
    lovelace = hass.data["lovelace"]
    resources: ResourceStorageCollection = (
        lovelace.resources if hasattr(lovelace, "resources") else lovelace["resources"]
    )
    is_store = isinstance(resources, ResourceStorageCollection)

    # force load storage
    await resources.async_get_info()

    # May be just update existing records
    for item in resources.async_items():
        if not item.get("url", "").startswith(url):
            continue

        return (item, resources, is_store)

    return (None, resources, is_store)
