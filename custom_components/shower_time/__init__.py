"""Hot Water Shower Time: a self-learning estimate of how long you can still shower."""
from __future__ import annotations

from pathlib import Path

import voluptuous as vol

from homeassistant.components import websocket_api
from homeassistant.components.frontend import add_extra_js_url
from homeassistant.components.http import StaticPathConfig
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import Platform
from homeassistant.core import HomeAssistant
from homeassistant.helpers import config_validation as cv, entity_registry as er
from homeassistant.helpers.typing import ConfigType

from .const import CARD_URL, DOMAIN
from .manager import ShowerTimeManager

PLATFORMS = [Platform.SENSOR, Platform.BINARY_SENSOR, Platform.BUTTON]
CONFIG_SCHEMA = cv.config_entry_only_config_schema(DOMAIN)

type ShowerTimeConfigEntry = ConfigEntry[ShowerTimeManager]


async def async_setup(hass: HomeAssistant, config: ConfigType) -> bool:
    """Serve the dashboard card so users do not need a separate frontend download."""
    card = Path(__file__).parent / "frontend" / "shower-time-card.js"
    await hass.http.async_register_static_paths([StaticPathConfig(CARD_URL, str(card), True)])
    version = hass.data.setdefault(DOMAIN, {}).get("version")
    if version is None:
        from homeassistant.loader import async_get_integration
        version = str((await async_get_integration(hass, DOMAIN)).version)
        hass.data[DOMAIN]["version"] = version
    add_extra_js_url(hass, f"{CARD_URL}?v={version}")
    websocket_api.async_register_command(hass, ws_history)
    return True


@websocket_api.websocket_command({
    vol.Required("type"): f"{DOMAIN}/history",
    vol.Required("entity_id"): str,
    vol.Optional("hours", default=24): vol.All(int, vol.Range(min=1, max=168)),
})
@websocket_api.async_response
async def ws_history(hass: HomeAssistant, connection: websocket_api.ActiveConnection, msg: dict) -> None:
    """History for the card, recomputed from the tank sensors so the graph is full from day one."""
    ent = er.async_get(hass).async_get(msg["entity_id"])
    entry = hass.config_entries.async_get_entry(ent.config_entry_id) if ent and ent.config_entry_id else None
    if entry is None or entry.domain != DOMAIN or not hasattr(entry, "runtime_data"):
        connection.send_error(msg["id"], "not_found", "Not a Hot Water Shower Time entity")
        return
    connection.send_result(msg["id"], {"points": await entry.runtime_data.async_history(msg["hours"])})


async def async_setup_entry(hass: HomeAssistant, entry: ShowerTimeConfigEntry) -> bool:
    manager = ShowerTimeManager(hass, entry)
    entry.runtime_data = manager
    await manager.async_start()
    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)
    entry.async_on_unload(entry.add_update_listener(_reload))
    return True


async def async_unload_entry(hass: HomeAssistant, entry: ShowerTimeConfigEntry) -> bool:
    ok = await hass.config_entries.async_unload_platforms(entry, PLATFORMS)
    if ok:
        await entry.runtime_data.async_stop()
    return ok


async def _reload(hass: HomeAssistant, entry: ShowerTimeConfigEntry) -> None:
    await hass.config_entries.async_reload(entry.entry_id)
