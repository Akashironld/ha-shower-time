"""Setting up, the dashboard card, diagnostics, reloading and the dashboard resource."""
from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

from homeassistant.components.frontend import DATA_EXTRA_JS_URL_ES5, DATA_EXTRA_MODULE_URL
from homeassistant.config_entries import ConfigEntryState
from homeassistant.core import HomeAssistant
from pytest_homeassistant_custom_component.components.recorder.common import async_wait_recording_done

from custom_components.shower_time import CARD_URL, _async_register_resource
from custom_components.shower_time.diagnostics import async_get_config_entry_diagnostics

from .conftest import HUMIDITY, setup_entry



async def test_entities_are_created(hass: HomeAssistant, entry) -> None:
    assert entry.state is ConfigEntryState.LOADED
    minutes = hass.states.get("sensor.hot_water_shower_time")
    assert minutes is not None and float(minutes.state) > 0
    for entity_id, state in (("binary_sensor.hot_water_hot_water_in_use", "off"),
                             ("binary_sensor.hot_water_showering", "off"),
                             ("sensor.hot_water_current_shower", "0"),
                             ("sensor.hot_water_showers_today", "0")):
        assert hass.states.get(entity_id).state == state, entity_id
    # niche sensors exist but are switched off for a new installation
    from homeassistant.helpers import entity_registry as er
    reg = er.async_get(hass)
    for entity_id in ("sensor.hot_water_showers_total", "sensor.hot_water_showers_left", "sensor.hot_water_hot_water"):
        ent = reg.async_get(entity_id)
        assert ent is not None and ent.disabled_by is er.RegistryEntryDisabler.INTEGRATION, entity_id
        assert hass.states.get(entity_id) is None
    assert hass.states.get("sensor.hot_water_shower_time").attributes["problems"] == []
    assert hass.states.get("event.hot_water_shower") is not None
    assert hass.states.get("button.hot_water_learn_now") is not None
    assert hass.states.get("sensor.hot_water_tank_charge") is not None


async def test_card_from_the_start_page_when_resources_are_in_yaml(hass: HomeAssistant, entry) -> None:
    """No dashboard resources to manage (YAML): the start page loads the card, in both frontend builds."""
    hass.http.async_register_static_paths.assert_called_once()
    path = hass.http.async_register_static_paths.call_args.args[0][0]
    assert path.url_path == CARD_URL and path.path.endswith("shower-time-card.js")
    modern, legacy = hass.data[DATA_EXTRA_MODULE_URL], hass.data[DATA_EXTRA_JS_URL_ES5]
    assert len(modern) == 1 and next(iter(modern)).startswith(CARD_URL + "?v=")
    assert modern == legacy


async def test_card_only_as_a_resource_when_dashboards_are_managed_in_the_ui(hass: HomeAssistant, deps) -> None:
    """Dashboards in the UI (the default): the card is a dashboard resource and NOT an early start-page module,
    which could run before Home Assistant's scoped element registry and be invisible ("Custom element doesn't
    exist" in the mobile apps). Legacy browsers still get it from the start page."""
    from custom_components.shower_time import async_setup as integration_setup
    res = _resources(hass)
    hass.data["lovelace"] = SimpleNamespace(resources=res)
    assert await integration_setup(hass, {})
    await hass.async_block_till_done()
    assert hass.data[DATA_EXTRA_MODULE_URL] == set()
    assert len(hass.data[DATA_EXTRA_JS_URL_ES5]) == 1
    urls = [i["url"] for i in res.async_items()]
    assert len(urls) == 1 and urls[0] == next(iter(hass.data[DATA_EXTRA_JS_URL_ES5]))


async def test_card_falls_back_to_the_start_page_when_the_resource_fails(hass: HomeAssistant, deps) -> None:
    from custom_components.shower_time import async_setup as integration_setup
    res = _resources(hass)
    res.async_create_item = AsyncMock(side_effect=RuntimeError("storage broken"))
    hass.data["lovelace"] = SimpleNamespace(resources=res)
    assert await integration_setup(hass, {})
    await hass.async_block_till_done()
    assert len(hass.data[DATA_EXTRA_MODULE_URL]) == 1


async def test_diagnostics(hass: HomeAssistant, entry) -> None:
    data = await async_get_config_entry_diagnostics(hass, entry)
    assert data["config"]["top_sensor"] == "sensor.tank_top"
    assert data["live"]["top"] == 55.0
    for key in ("learned", "ledger", "habits", "heat_loss", "legionella", "hot_water_use", "tank_readings_24h_5min",
                "signals_24h"):
        assert key in data, key
    assert "cop_learned" in data["ledger"]


async def test_diagnostics_show_what_the_bathroom_signal_saw(hass: HomeAssistant, deps) -> None:
    water = ("sensor.water_flow", {"unit_of_measurement": "L/min", "device_class": "volume_flow_rate"})
    hass.states.async_set(HUMIDITY, "60", {"unit_of_measurement": "%", "device_class": "humidity"})
    hass.states.async_set(water[0], "0", water[1])
    entry = await setup_entry(hass, {"bathroom_sensor": HUMIDITY, "water_sensor": water[0]})
    hass.states.async_set(HUMIDITY, "71.25", {"unit_of_measurement": "%", "device_class": "humidity"})
    hass.states.async_set(water[0], "7.5", water[1])
    await async_wait_recording_done(hass)
    data = await async_get_config_entry_diagnostics(hass, entry)
    assert data["signals_24h"]["bathroom"][-1][1] == 71.25
    assert data["signals_24h"]["water_meter"][-1][1] == 7.5


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
        assert [(i["url"], i["type"]) for i in items] == [(CARD_URL + "?v=2", "module")], style
        before = dict(items[0])
        await _async_register_resource(hass, CARD_URL + "?v=2")    # already up to date: left alone
        assert [dict(i) for i in res.async_items() if i["url"].startswith(CARD_URL)] == [before], style
    hass.data.pop("lovelace")
    await _async_register_resource(hass, CARD_URL + "?v=3")        # no dashboards: nothing to do, no error


async def test_a_known_tank_type_counts_the_water_above_the_top_sensor(hass: HomeAssistant, deps) -> None:
    """#14: a Weheat WBL-200 holds hot water above its top sensor; the old model left it out."""
    plain = await setup_entry(hass, top=52.8, bottom=52.5)
    simple = float(hass.states.get("sensor.hot_water_shower_time").state)
    assert hass.states.get("sensor.hot_water_learned_model").attributes["sensor_heights"] is None
    await hass.config_entries.async_unload(plain.entry_id)
    await hass.config_entries.async_remove(plain.entry_id)
    await hass.async_block_till_done()
    await setup_entry(hass, data={"tank_type": "weheat_wbl200", "learn_sensor_heights": True}, top=52.8, bottom=52.5)
    layered = float(hass.states.get("sensor.hot_water_shower_time").state)
    heights = hass.states.get("sensor.hot_water_learned_model").attributes["sensor_heights"]
    assert heights["source"] == "weheat_wbl200" and heights["learning"]
    assert layered > simple * 1.3


async def _tank_history(hass: HomeAssistant, freezer) -> None:
    """Recorded: a low tank, a reheat to 52.8 / 52.5, then a shower that cooled the bottom."""
    from datetime import timedelta
    from .conftest import tank
    for top, bottom, minutes in ((44.0, 20.0, 30), (50.0, 40.0, 20), (52.8, 52.5, 60), (52.6, 30.0, 10)):
        tank(hass, top, bottom)
        await hass.async_block_till_done()
        freezer.tick(timedelta(minutes=minutes))
    await async_wait_recording_done(hass)


async def test_layer_model_is_right_straight_after_install(hass: HomeAssistant, deps, freezer) -> None:
    """The full-tank readings and the hot layer come from the history: no waiting for the next reheat (#14)."""
    await _tank_history(hass, freezer)
    entry = await setup_entry(hass, data={"tank_type": "weheat_wbl200"}, top=52.6, bottom=30.0)
    m = entry.runtime_data
    await hass.async_block_till_done(wait_background_tasks=True)
    assert m.full_ref_state == [52.8, 52.5]
    assert m.hot_top >= 52.6
    full = m.ref_minutes()
    assert 0 < m.fill_percent() < 100 and full > m.estimate.minutes


async def test_layer_model_after_updating_from_an_older_version(hass: HomeAssistant, deps, freezer, hass_storage) -> None:
    """Stored data from 0.13 has a reference but no full-tank readings: they are looked up once at startup."""
    from custom_components.shower_time import model
    await _tank_history(hass, freezer)
    entry_id = "old_entry"
    hass_storage[f"shower_time.{entry_id}"] = {"version": 1, "key": f"shower_time.{entry_id}", "data": {
        "profile": "generic", "params": model.new_params("generic"), "full_ref": 20.0, "full_ref_avg": 52.0,
        "full_ref_at": "2026-10-15T10:00:00+00:00"}}
    from pytest_homeassistant_custom_component.common import MockConfigEntry
    from .conftest import TOP, BOTTOM, tank
    tank(hass, 52.6, 30.0)
    entry = MockConfigEntry(domain="shower_time", title="Hot water", unique_id=TOP, entry_id=entry_id,
                            data={"top_sensor": TOP, "bottom_sensor": BOTTOM, "tank_volume": 200,
                                  "temperature_mode": "auto", "profile": "generic", "flow_source": "unknown",
                                  "tank_type": "weheat_wbl200"})
    entry.add_to_hass(hass)
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done(wait_background_tasks=True)
    m = entry.runtime_data
    assert m.full_ref_state == [52.8, 52.5]
    assert m.params.get("geo") and 0 < m.fill_percent() < 100


async def test_a_part_used_tank_that_stands_moves_to_the_simple_model(hass: HomeAssistant, deps, freezer) -> None:
    """#10: the same two readings mean less hot water the longer a part-used tank stands; the blur is kept."""
    from datetime import timedelta

    from homeassistant.util import dt as dt_util

    from custom_components.shower_time import model

    from .conftest import tank
    entry = await setup_entry(hass, data={"tank_type": "weheat_wbl200"}, top=48.4, bottom=24.8)
    m = entry.runtime_data
    await hass.async_block_till_done(wait_background_tasks=True)
    fresh = m.estimate.minutes
    for hour in range(14):                       # a night standing: the top cools, the bottom slowly warms
        freezer.tick(timedelta(hours=1))
        tank(hass, 48.4 - 0.01 * (hour + 1), 24.8)
        await hass.async_block_till_done()
    assert m.blur > 0.9
    simple = model.estimate({k: v for k, v in m.params.items() if k != "geo"}, m.top_value, m.bottom_value,
                            volume=200, shower_temp=m.shower_temp, month=dt_util.now().month,
                            doy=dt_util.now().timetuple().tm_yday, ema=m.ema_now).minutes
    assert m.estimate.minutes < fresh / 2 and abs(m.estimate.minutes - simple) < 0.15 * fresh
    assert m.blurred_minutes > fresh / 3
    assert m._data()["blur"] == round(m.blur, 4)
    diag = await async_get_config_entry_diagnostics(hass, entry)
    assert diag["live"]["blur"] > 0.9
    tank(hass, 55.0, 54.0)                       # reheated: hot water past the bottom sensor again
    await hass.async_block_till_done()
    assert m.blur == 0.0 and m.blurred_minutes == 0.0
