from pathlib import Path

from homeassistant.components.frontend import (
    add_extra_js_url,
    async_register_built_in_panel,
)
from homeassistant.components.http import StaticPathConfig
from homeassistant.core import HomeAssistant

from .const import DOMAIN


async def async_register_frontend(hass: HomeAssistant) -> None:
    """Register the frontend."""
    path = Path(__file__).parent / "www" / "my_demo.js"
    url = f"/hacsfiles/{DOMAIN}/my_demo.js"

    await hass.http.async_register_static_paths([StaticPathConfig(url, path, True)])

    add_extra_js_url(hass, url)

    # Add to sidepanel if needed
    if DOMAIN not in hass.data.get("frontend_panels", {}):
        async_register_built_in_panel(
            hass,
            component_name="custom",
            sidebar_title="Docker",
            sidebar_icon="",
            frontend_url_path=DOMAIN,
            config={
                "_panel_custom": {
                    "name": "hacs-frontend",
                    "embed_iframe": True,
                    "trust_external": False,
                    "js_url": url,
                }
            },
            require_admin=True,
        )
