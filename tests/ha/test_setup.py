"""Setting up, the dashboard card, diagnostics, reloading and the dashboard resource."""
from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

from homeassistant.components.frontend import DATA_EXTRA_JS_URL_ES5, DATA_EXTRA_MODULE_URL
from homeassistant.config_entries import ConfigEntryState
from homeassistant.core import HomeAssistant

from custom_components.shower_time import CARD_URL, _async_register_resource
from custom_components.shower_time.diagnostics import async_get_config_entry_diagnostics



async def test_entities_are_created(hass: HomeAssistant, entry) -> None:
    assert entry.state is ConfigEntryState.LOADED
    minutes = hass.states.get("sensor.hot_water_shower_time")
    assert minutes is not None and float(minutes.state) > 0
    for entity_id, state in (("binary_sensor.hot_water_hot_water_in_use", "off"),
                             ("binary_sensor.hot_water_showering", "off"),
                             ("sensor.hot_water_current_shower", "0"),
                             ("sensor.hot_water_showers_today", "0"),
                             ("sensor.hot_water_showers_total", "0")):
        assert hass.states.get(entity_id).state == state, entity_id
    assert hass.states.get("event.hot_water_shower") is not None
    assert hass.states.get("button.hot_water_learn_now") is not None
    assert hass.states.get("sensor.hot_water_tank_charge") is not None


async def test_card_is_served_for_both_frontend_builds(hass: HomeAssistant, entry) -> None:
    hass.http.async_register_static_paths.assert_called_once()
    path = hass.http.async_register_static_paths.call_args.args[0][0]
    assert path.url_path == CARD_URL and path.path.endswith("shower-time-card.js")
    modern, legacy = hass.data[DATA_EXTRA_MODULE_URL], hass.data[DATA_EXTRA_JS_URL_ES5]
    assert len(modern) == 1 and next(iter(modern)).startswith(CARD_URL + "?v=")
    assert modern == legacy


async def test_diagnostics(hass: HomeAssistant, entry) -> None:
    data = await async_get_config_entry_diagnostics(hass, entry)
    assert data["config"]["top_sensor"] == "sensor.tank_top"
    assert data["live"]["top"] == 55.0
    for key in ("learned", "ledger", "habits", "heat_loss", "legionella", "hot_water_use", "tank_readings_24h_5min"):
        assert key in data, key


async def test_reload_and_unload(hass: HomeAssistant, entry) -> None:
    assert await hass.config_entries.async_reload(entry.entry_id)
    await hass.async_block_till_done()
    assert entry.state is ConfigEntryState.LOADED
    assert hass.states.get("sensor.hot_water_shower_time") is not None
    assert await hass.config_entries.async_unload(entry.entry_id)
    await hass.async_block_till_done()
    assert entry.state is ConfigEntryState.NOT_LOADED


async def test_works_without_a_bottom_sensor(hass: HomeAssistant, deps) -> None:
    from pytest_homeassistant_custom_component.common import MockConfigEntry
    hass.states.async_set("sensor.only_top", "58", {"unit_of_measurement": "°C", "device_class": "temperature"})
    entry = MockConfigEntry(domain="shower_time", title="Boiler", unique_id="sensor.only_top",
                            data={"top_sensor": "sensor.only_top", "tank_volume": 150, "temperature_mode": "auto",
                                  "profile": "generic", "flow_source": "unknown"})
    entry.add_to_hass(hass)
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()
    assert float(hass.states.get("sensor.boiler_shower_time").state) > 0
    assert hass.states.get("binary_sensor.boiler_enough_for_today").state == "unavailable"   # needs showers seen


def _resources(hass: HomeAssistant):
    from homeassistant.components.lovelace.resources import ResourceStorageCollection
    ll_config = MagicMock()
    ll_config.async_load = AsyncMock(return_value={})
    return ResourceStorageCollection(hass, ll_config)


async def test_card_becomes_a_dashboard_resource_old_and_new_style(hass: HomeAssistant, deps) -> None:
    """Home Assistant keeps the resources in a dict (up to early 2025) or an object (since): both work, the
    version is brought up to date instead of adding a second entry."""
    for style in ("dict", "object"):
        res = _resources(hass)
        hass.data["lovelace"] = {"resources": res} if style == "dict" else SimpleNamespace(resources=res)
        await _async_register_resource(hass, CARD_URL + "?v=1")
        await _async_register_resource(hass, CARD_URL + "?v=2")
        items = [i for i in res.async_items() if i["url"].startswith(CARD_URL)]
        assert [(i["url"], i["res_type"]) for i in items] == [(CARD_URL + "?v=2", "module")], style
    hass.data.pop("lovelace")
    await _async_register_resource(hass, CARD_URL + "?v=3")        # no dashboards: nothing to do, no error
