"""Hot Water Shower Time: a self-learning estimate of how long you can still shower."""
from __future__ import annotations

from pathlib import Path

from homeassistant.components.frontend import add_extra_js_url
from homeassistant.components.http import StaticPathConfig
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import Platform
from homeassistant.core import HomeAssistant
from homeassistant.helpers import config_validation as cv
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
    return True


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
