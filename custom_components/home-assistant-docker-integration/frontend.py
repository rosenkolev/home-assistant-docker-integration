from pathlib import Path

from homeassistant.components.frontend import (
    add_extra_js_url,
    async_register_built_in_panel,
)
from homeassistant.components.http import StaticPathConfig
from homeassistant.components.lovelace.resources import ResourceStorageCollection
from homeassistant.core import HomeAssistant

from .const import _LOGGER, DOMAIN


async def async_register_frontend_panel(
    hass: HomeAssistant, path: str, name: str, title: str, icon: str, version: str
) -> None:
    abs_path = Path(__file__).parent / path
    url = f"/hacsfiles/{DOMAIN}/{path}"

    await hass.http.async_register_static_paths([StaticPathConfig(url, abs_path, True)])
    # await async_add_or_update_resource(hass, url, version)

    if DOMAIN not in hass.data.get("frontend_panels", {}):
        async_register_built_in_panel(
            hass,
            component_name="custom",
            sidebar_title="Docker",
            sidebar_icon="",
            frontend_url_path=DOMAIN,
            config={
                "_panel_custom": {
                    "name": name,
                    "embed_iframe": False,
                    "trust_external": False,
                    "js_url": f"{url}?ver={version}",
                }
            },
            require_admin=True,
        )


async def async_add_or_update_resource(hass: HomeAssistant, url: str, ver: str) -> bool:
    """Add extra JS module for lovelace mode YAML and new lovelace resource
    for mode GUI. It's better to add extra JS for all modes, because it has
    random url to avoid problems with the cache. But chromecast don't support
    extra JS urls and can't load custom card.
    """
    lovelace = hass.data["lovelace"]
    resources: ResourceStorageCollection = (
        lovelace.resources if hasattr(lovelace, "resources") else lovelace["resources"]
    )

    # force load storage
    await resources.async_get_info()

    url2 = f"{url}?v={ver}"

    # May be just update existing records
    for item in resources.async_items():
        if not item.get("url", "").startswith(url):
            continue

        # no need to update
        if item["url"].endswith(ver):
            return False

        _LOGGER.debug(f"Update lovelace resource to: {url2}")

        if isinstance(resources, ResourceStorageCollection):
            await resources.async_update_item(
                item["id"], {"res_type": "module", "url": url2}
            )
        else:
            # not the best solution, but what else can we do
            item["url"] = url2

        return True

    if isinstance(resources, ResourceStorageCollection):
        _LOGGER.debug(f"Add new lovelace resource: {url2}")
        await resources.async_create_item({"res_type": "module", "url": url2})
    else:
        _LOGGER.debug(f"Add extra JS module: {url2}")
        add_extra_js_url(hass, url2)

    return True
