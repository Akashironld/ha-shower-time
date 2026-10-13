"""Hot Water Shower Time: a self-learning estimate of how long you can still shower."""
from __future__ import annotations

import logging
from pathlib import Path

import voluptuous as vol

from homeassistant.components import websocket_api
from homeassistant.components.frontend import add_extra_js_url
from homeassistant.components.http import StaticPathConfig
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import EVENT_HOMEASSISTANT_STARTED, Platform
from homeassistant.core import CoreState, Event, HomeAssistant
from homeassistant.helpers import config_validation as cv, entity_registry as er, issue_registry as ir
from homeassistant.helpers.typing import ConfigType

from .const import CARD_URL, DOMAIN
from .manager import ShowerTimeManager

_LOGGER = logging.getLogger(__name__)

PLATFORMS = [Platform.SENSOR, Platform.BINARY_SENSOR, Platform.BUTTON, Platform.EVENT]
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
    # Both frontend builds: older browsers (e.g. wall tablets) get the legacy build, which only loads es5 URLs.
    add_extra_js_url(hass, f"{CARD_URL}?v={version}")
    add_extra_js_url(hass, f"{CARD_URL}?v={version}", es5=True)
    # Also a dashboard resource, like cards installed through HACS: dashboards load their resources each time they
    # open, so the card is there even when an app shows a cached start page (seen on iOS). Same URL, loaded once.
    url = f"{CARD_URL}?v={version}"
    if hass.state is CoreState.running:
        hass.async_create_task(_async_register_resource(hass, url))
    else:
        async def _started(_event: Event) -> None:
            await _async_register_resource(hass, url)
        hass.bus.async_listen_once(EVENT_HOMEASSISTANT_STARTED, _started)
    websocket_api.async_register_command(hass, ws_history)
    return True


def _resources(hass: HomeAssistant):
    """The dashboard resources, when they are managed in the UI (not in YAML).

    Home Assistant keeps them under hass.data["lovelace"]: a dict up to early 2025, an object since. Only the
    storage collection can be changed; YAML resources are left alone."""
    try:
        from homeassistant.components.lovelace.resources import ResourceStorageCollection
    except ImportError:  # pragma: no cover - very old or changed Home Assistant
        return None
    data = hass.data.get("lovelace")
    res = data.get("resources") if isinstance(data, dict) else getattr(data, "resources", None)
    return res if isinstance(res, ResourceStorageCollection) else None


async def _async_register_resource(hass: HomeAssistant, url: str) -> None:
    """Add the card to the dashboard resources, or bring its version up to date."""
    res = _resources(hass)
    if res is None:
        return
    try:
        await res.async_get_info()                       # loads the collection
        for item in res.async_items():
            if str(item.get("url", "")).split("?")[0] == CARD_URL:
                if item.get("url") != url or item.get("type") != "module":     # stored as "type"
                    await res.async_update_item(item["id"], {"res_type": "module", "url": url})
                return
        await res.async_create_item({"res_type": "module", "url": url})
    except Exception:  # noqa: BLE001 - the card still loads as an extra module
        _LOGGER.warning("Could not add the Shower time card to the dashboard resources", exc_info=True)


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


async def async_remove_entry(hass: HomeAssistant, entry: ShowerTimeConfigEntry) -> None:
    """Removed by the user: take its repair notices with it, and the dashboard resource with the last one."""
    if not [e for e in hass.config_entries.async_entries(DOMAIN) if e.entry_id != entry.entry_id]:
        if (res := _resources(hass)) is not None:
            try:
                await res.async_get_info()
                for item in res.async_items():
                    if str(item.get("url", "")).split("?")[0] == CARD_URL:
                        await res.async_delete_item(item["id"])
            except Exception:  # noqa: BLE001
                _LOGGER.debug("Could not remove the card from the dashboard resources", exc_info=True)
    for issue_id in [i for (d, i) in ir.async_get(hass).issues if d == DOMAIN and i.startswith(entry.entry_id)]:
        ir.async_delete_issue(hass, DOMAIN, issue_id)


async def _reload(hass: HomeAssistant, entry: ShowerTimeConfigEntry) -> None:
    await hass.config_entries.async_reload(entry.entry_id)
