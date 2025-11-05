from pathlib import Path

from homeassistant.components.frontend import (
    add_extra_js_url,
    async_register_built_in_panel,
)
from homeassistant.components.http import StaticPathConfig
from homeassistant.core import HomeAssistant

from .const import DOMAIN


async def async_register_frontend_panel(
    hass: HomeAssistant, path: str, name: str, title: str, icon: str
) -> None:
    abs_path = Path(__file__).parent / path
    url = f"/hacsfiles/{DOMAIN}/{path}"

    await hass.http.async_register_static_paths([StaticPathConfig(url, abs_path, True)])
    add_extra_js_url(hass, url)

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
                    "js_url": url,
                }
            },
            require_admin=True,
        )
