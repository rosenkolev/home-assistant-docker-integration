from pathlib import Path

from homeassistant.components.frontend import (
    DATA_PANELS,
    add_extra_js_url,
    async_register_built_in_panel,
    async_remove_panel,
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


class FrontendResourcesRegistry:
    def __init__(self, hass: HomeAssistant, version: str):
        self.hass = hass
        self.version = version
        self.registered_resources = set()
        self.registered_panels = set()

    async def async_register_resource(self, url: str):
        resources: ResourceStorageCollection = self.hass.data[LOVELACE_DATA].resources
        url_with_version = f"{url}?v={self.version}"

        if isinstance(resources, ResourceStorageCollection):
            # when storage
            item = await __async_find_lovelace_resource_by_url(resources, url)
            if item is None:
                await resources.async_create_item(
                    {"res_type": "module", "url": url_with_version}
                )
            elif not item["url"].endswith(f"?v={self.version}"):
                await resources.async_update_item(
                    item["id"], {"res_type": "module", "url": url_with_version}
                )
        else:
            _LOGGER.debug(f"Add extra JS module: {url_with_version}")
            add_extra_js_url(self.hass, url_with_version)

        self.registered_resources.add(url_with_version)

    def async_register_yaml_panel(
        self,
        url: str,
        config_file: str,
        title: str,
        icon: str,
        require_admin: bool,
    ):
        if DOMAIN not in self.hass.data.get(DATA_PANELS, {}):
            dashboards = self.hass.data[LOVELACE_DATA].dashboards
            existing = dashboards.get(url)
            if not existing:
                dashboards[url] = LovelaceYAML(
                    self.hass,
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
                    self.hass,
                    component_name="lovelace",
                    sidebar_title=title,
                    sidebar_icon=icon,
                    frontend_url_path=url,
                    config={"mode": MODE_YAML},
                    require_admin=require_admin,
                    config_panel_domain=DOMAIN,
                )

        self.registered_panels.add(url)

    async def async_unload_frontend_resources(self):
        resources: ResourceStorageCollection = self.hass.data[LOVELACE_DATA].resources
        is_resource_storage = isinstance(resources, ResourceStorageCollection)
        # remove all lovelace resources
        for url in self.registered_resources:
            if is_resource_storage:
                await resources.async_delete_item(url)
            else:
                remove_extra_js_url(url)

        # remove all panels
        for url in self.registered_panels:
            await async_remove_panel(url)


async def async_register_static_path_to_hass_router(
    hass: HomeAssistant, url: str, path: str, cache_headers=True
):
    abs_path = Path(__file__).parent / path
    await hass.http.async_register_static_paths(
        [StaticPathConfig(url, abs_path, cache_headers)]
    )

    return url


async def __async_find_lovelace_resource_by_url(resources, url: str):
    # force load storage
    await resources.async_get_info()

    # May be just update existing records
    for item in resources.async_items():
        if item.get("url", "").startswith(url):
            return item

    return None
