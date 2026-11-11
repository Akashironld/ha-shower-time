"""Saving on unload, availability, reconfiguring the tank sensors, and the card's history data."""
from __future__ import annotations

from datetime import timedelta
from unittest.mock import AsyncMock, MagicMock

from homeassistant.config_entries import ConfigEntryState
from homeassistant.core import HomeAssistant
from homeassistant.data_entry_flow import FlowResultType
from homeassistant.helpers import entity_registry as er
from homeassistant.util import dt as dt_util

from custom_components.shower_time.manager import reheat_events

from pytest_homeassistant_custom_component.components.recorder.common import async_wait_recording_done

from .conftest import BOTTOM, DOMAIN, TEMP_ATTRS, TOP, setup_entry, tank
from .test_setup import _tank_history


async def test_state_survives_an_immediate_reload(hass: HomeAssistant, deps) -> None:
    entry = await setup_entry(hass)
    m = entry.runtime_data
    await hass.async_block_till_done(wait_background_tasks=True)
    m.last_learn = "2026-10-15T10:00:00+00:00"
    m.ledger.other_ended(m, "tap", dt_util.utcnow(), 3.0, 0.4)
    assert await hass.config_entries.async_unload(entry.entry_id)
    assert m._stopped
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done(wait_background_tasks=True)
    m2 = entry.runtime_data
    assert m2 is not m
    assert m2.last_learn == "2026-10-15T10:00:00+00:00"
    assert len(m2.ledger.other) == 1 and m2.ledger.other[0]["kind"] == "tap"


async def test_a_stopped_manager_does_not_learn_or_save(hass: HomeAssistant, deps) -> None:
    entry = await setup_entry(hass)
    m = entry.runtime_data
    await m.async_stop()
    m._store.async_save = AsyncMock()
    m._store.async_delay_save = MagicMock()
    m._learn = AsyncMock()
    before = m.last_learn
    await m.async_learn()
    await m._nightly(None)
    m.request_save()
    m._save_later(5)
    m._learn.assert_not_called()
    m._store.async_save.assert_not_called()
    m._store.async_delay_save.assert_not_called()
    assert m.last_learn == before and not m.learning


async def test_stop_before_the_store_was_read_saves_nothing(hass: HomeAssistant, deps) -> None:
    from custom_components.shower_time.manager import ShowerTimeManager
    entry = await setup_entry(hass)
    await hass.config_entries.async_unload(entry.entry_id)
    m = ShowerTimeManager(hass, entry)              # never started: its empty data must not reach the store
    m._store.async_save = AsyncMock()
    await m.async_stop()
    m._store.async_save.assert_not_called()


async def test_totals_stay_available_while_the_tank_sensor_is_not(hass: HomeAssistant, deps) -> None:
    entry = await setup_entry(hass)
    reg = er.async_get(hass)
    uid = lambda key: reg.async_get_entity_id("sensor", DOMAIN, f"{entry.entry_id}_{key}")
    energy, today, minutes = uid("shower_energy_total"), uid("showers_today"), uid("shower_minutes")
    assert hass.states.get(minutes).state != "unavailable"
    hass.states.async_set(TOP, "unavailable", TEMP_ATTRS)
    await hass.async_block_till_done()
    assert hass.states.get(minutes).state == "unavailable"
    assert hass.states.get(energy).state != "unavailable"
    assert hass.states.get(today).state != "unavailable"


async def test_minutes_sensor_exposes_the_sensor_heights(hass: HomeAssistant, deps) -> None:
    entry = await setup_entry(hass, data={"tank_type": "weheat_wbl200"})
    h = hass.states.get("sensor.hot_water_shower_time").attributes["sensor_heights"]
    geo = entry.runtime_data.params["geo"]
    assert h == {"top": geo["top"], "bottom": geo["bottom"]} and 0 < h["bottom"] < h["top"] < 1


async def test_sensor_heights_are_none_without_the_layer_model(hass: HomeAssistant, deps) -> None:
    entry = await setup_entry(hass, data={"tank_type": "other"})
    assert "geo" not in entry.runtime_data.params or not entry.runtime_data.params["geo"]
    assert hass.states.get("sensor.hot_water_shower_time").attributes["sensor_heights"] is None


def test_reheat_events_from_the_average() -> None:
    ms = lambda m: m * 60_000
    pts = [[ms(0), 10, 40.0], [ms(5), 9, 39.8], [ms(10), 12, 41.0], [ms(15), 15, 43.0], [ms(20), 14, 42.8],
           [ms(25), 18, 45.0], [ms(30), 18, 45.0],
           [ms(300), 10, 40.0], [ms(305), 10, 40.5]]               # a rise of only 0.5 K: not a reheat
    assert reheat_events(pts) == [{"start": ms(5), "end": ms(25), "kind": "reheat"}]
    assert reheat_events([]) == []


async def test_history_events(hass: HomeAssistant, deps, freezer) -> None:
    await _tank_history(hass, freezer)
    entry = await setup_entry(hass, top=52.6, bottom=30.0)
    m = entry.runtime_data
    await hass.async_block_till_done(wait_background_tasks=True)
    now = dt_util.utcnow()
    m.ledger.log = [{"start": (now - timedelta(minutes=50)).isoformat(), "kind": "shower", "minutes": 8.0},
                    {"start": (now - timedelta(minutes=80)).isoformat(), "kind": "bath", "minutes": 5.0, "duration": 15.0}]
    m.ledger.other = [{"start": (now - timedelta(minutes=30)).isoformat(), "kind": "tap", "minutes": 2.0},
                      {"start": (now - timedelta(hours=40)).isoformat(), "kind": "tap", "minutes": 2.0}]
    res = await m.async_events(3)
    assert res["points"] == await m.async_history(3)
    ev = res["events"]
    assert [e["start"] for e in ev] == sorted(e["start"] for e in ev)
    kinds = [e["kind"] for e in ev]
    assert kinds.count("bath") == 1 and kinds.count("shower") == 1 and kinds.count("other") == 1
    assert "reheat" in kinds                                    # the 44 -> 52.8 reheat in the recorded tank
    bath = next(e for e in ev if e["kind"] == "bath")
    assert bath["end"] - bath["start"] == 15 * 60_000 and isinstance(bath["start"], int)


async def test_reconfigure_changes_the_sensors_and_keeps_the_data(hass: HomeAssistant, deps) -> None:
    entry = await setup_entry(hass)
    m = entry.runtime_data
    await hass.async_block_till_done(wait_background_tasks=True)
    m.ledger.other_ended(m, "tap", dt_util.utcnow(), 3.0, 0.4)
    hass.states.async_set("sensor.new_top", "54", TEMP_ATTRS)
    hass.states.async_set("sensor.new_bottom", "40", TEMP_ATTRS)
    result = await entry.start_reconfigure_flow(hass)
    assert result["type"] is FlowResultType.FORM and result["step_id"] == "reconfigure"
    same = await hass.config_entries.flow.async_configure(
        result["flow_id"], {"top_sensor": "sensor.new_top", "bottom_sensor": "sensor.new_top"})
    assert same["errors"] == {"bottom_sensor": "same_sensor"}
    done = await hass.config_entries.flow.async_configure(
        result["flow_id"], {"top_sensor": "sensor.new_top", "bottom_sensor": "sensor.new_bottom"})
    await hass.async_block_till_done(wait_background_tasks=True)
    assert done["type"] is FlowResultType.ABORT and done["reason"] == "reconfigure_successful"
    assert entry.unique_id == "sensor.new_top" and entry.data["top_sensor"] == "sensor.new_top"
    assert entry.data["bottom_sensor"] == "sensor.new_bottom" and entry.data["tank_volume"] == 200
    assert entry.state is ConfigEntryState.LOADED
    m2 = entry.runtime_data
    assert m2 is not m and m2.top == "sensor.new_top" and len(m2.ledger.other) == 1


async def test_reconfigure_refuses_a_top_sensor_another_entry_uses(hass: HomeAssistant, deps) -> None:
    from pytest_homeassistant_custom_component.common import MockConfigEntry
    entry = await setup_entry(hass)
    hass.states.async_set("sensor.other_top", "54", TEMP_ATTRS)
    other = MockConfigEntry(domain=DOMAIN, title="Other", unique_id="sensor.other_top",
                            data={"top_sensor": "sensor.other_top", "tank_volume": 100, "temperature_mode": "auto",
                                  "profile": "generic", "flow_source": "unknown"})
    other.add_to_hass(hass)
    result = await entry.start_reconfigure_flow(hass)
    done = await hass.config_entries.flow.async_configure(result["flow_id"], {"top_sensor": "sensor.other_top"})
    assert done["type"] is FlowResultType.ABORT and done["reason"] == "unique_id_in_use"
    assert entry.unique_id == TOP


async def _rising_tank(hass: HomeAssistant, freezer, start: float, step: float, steps: int) -> None:
    """Recorded: a tank that gains ``step`` K every 5 minutes (both sensors), then stays."""
    for k in range(steps + 1):
        tank(hass, round(start + k * step, 2), round(start - 6 + k * step, 2))
        await hass.async_block_till_done()
        freezer.tick(timedelta(minutes=5))
    await async_wait_recording_done(hass)


async def test_a_slow_steady_reheat_is_one_band(hass: HomeAssistant, deps, freezer) -> None:
    await _rising_tank(hass, freezer, 40.0, 0.15, 30)            # +4.5 K in 2.5 hours
    entry = await setup_entry(hass, top=44.5, bottom=38.5)
    res = await entry.runtime_data.async_events(4)
    bands = [e for e in res["events"] if e["kind"] == "reheat"]
    assert len(bands) == 1
    assert 140 * 60_000 <= bands[0]["end"] - bands[0]["start"] <= 155 * 60_000


async def test_a_small_top_up_is_no_reheat_band(hass: HomeAssistant, deps, freezer) -> None:
    await _rising_tank(hass, freezer, 40.0, 0.15, 10)            # +1.5 K: under the 2 K a band needs
    entry = await setup_entry(hass, top=41.5, bottom=35.5)
    res = await entry.runtime_data.async_events(4)
    assert [e for e in res["events"] if e["kind"] == "reheat"] == []
