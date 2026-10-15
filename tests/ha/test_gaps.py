"""Gaps in the tank readings — a heat pump's cloud that stops for a while, a sensor that is unavailable, a stale
reading after a restart: hot water used meanwhile is logged once, from the heat it took, instead of being taken
for a bath that filled in seconds. And a quiet night is not a gap."""
from __future__ import annotations

from datetime import datetime, timedelta

from homeassistant.core import HomeAssistant
from homeassistant.helpers import device_registry as dr, entity_registry as er
from homeassistant.util import dt as dt_util
from pytest_homeassistant_custom_component.common import MockConfigEntry, async_fire_time_changed

from .conftest import HUMIDITY, TEMP_ATTRS, setup_entry, tank
from .test_live import SHOWERING, state, step, wait

OUTSIDE = "sensor.heat_pump_outside"
HUMIDITY_ATTRS = {"unit_of_measurement": "%", "device_class": "humidity"}


def heat_pump_device(hass: HomeAssistant) -> None:
    """The tank sensors and an outside temperature on one device, like a heat pump integration makes them."""
    src = MockConfigEntry(domain="test")
    src.add_to_hass(hass)
    dev = dr.async_get(hass).async_get_or_create(config_entry_id=src.entry_id, identifiers={("test", "heat_pump")})
    reg = er.async_get(hass)
    for uid, obj in (("top", "tank_top"), ("bottom", "tank_bottom"), ("outside", "heat_pump_outside")):
        reg.async_get_or_create("sensor", "test", uid, suggested_object_id=obj, device_id=dev.id,
                                original_device_class="temperature", config_entry=src)
    hass.states.async_set(OUTSIDE, "12.0", TEMP_ATTRS)


async def minutes(hass: HomeAssistant, freezer, n: float, heat_pump_reports: bool) -> None:
    """Let time pass in 15 s steps; a reporting heat pump changes its outside temperature every 2 minutes."""
    for i in range(int(n * 4)):
        freezer.tick(timedelta(seconds=15))
        if heat_pump_reports and i % 8 == 7:
            value = float(hass.states.get(OUTSIDE).state)
            hass.states.async_set(OUTSIDE, f"{value + (0.1 if i % 16 == 7 else -0.1):.1f}", TEMP_ATTRS)
        async_fire_time_changed(hass)
        await hass.async_block_till_done()


def local(iso: str) -> datetime:
    return dt_util.as_utc(datetime.fromisoformat(iso))


async def test_a_shower_hidden_by_an_outage_is_logged_once_from_its_heat(hass: HomeAssistant, deps, freezer) -> None:
    """The heat pump stops reporting for 45 minutes; someone showers meanwhile (the bathroom fan says when). When
    the readings come back the tank is far lower: one shower, its length from the heat, starting with the fan."""
    heat_pump_device(hass)
    hass.states.async_set(HUMIDITY, "60", HUMIDITY_ATTRS)
    entry = await setup_entry(hass, {"bathroom_sensor": HUMIDITY})
    m = entry.runtime_data
    await minutes(hass, freezer, 10, heat_pump_reports=True)
    await minutes(hass, freezer, 20, heat_pump_reports=False)          # the outage begins
    hass.states.async_set(HUMIDITY, "68", HUMIDITY_ATTRS)
    showered_at = dt_util.utcnow()
    await minutes(hass, freezer, 25, heat_pump_reports=False)
    hass.states.async_set(OUTSIDE, "11.6", TEMP_ATTRS)                   # back: everything at once
    tank(hass, 54.6, 28.8)
    await hass.async_block_till_done()
    assert state(hass, SHOWERING) == "off"                               # not followed live: it is over
    await minutes(hass, freezer, 2, heat_pump_reports=True)
    assert state(hass, "sensor.hot_water_showers_today") == "1"
    last = m.ledger.log[-1]
    assert last["kind"] == "shower" and last["estimated"] is True
    assert abs(local(last["start"]) - showered_at) < timedelta(minutes=1)
    assert 8 < last["minutes"] < 30          # 26 K off the bottom: about 8 K of tank average, 0.84 K per minute
    assert float(state(hass, "sensor.hot_water_other_hot_water_today")) == 0
    ended = hass.states.get("event.hot_water_shower").attributes
    assert ended["event_type"] == "shower_ended" and ended["estimated"] is True
    await minutes(hass, freezer, 10, heat_pump_reports=True)             # nothing more comes of it
    assert len(m.ledger.log) == 1 and state(hass, "sensor.hot_water_showers_today") == "1"


async def test_an_outage_without_hot_water_logs_nothing(hass: HomeAssistant, deps, freezer) -> None:
    heat_pump_device(hass)
    entry = await setup_entry(hass)
    await minutes(hass, freezer, 6, heat_pump_reports=True)
    await minutes(hass, freezer, 50, heat_pump_reports=False)
    hass.states.async_set(OUTSIDE, "11.6", TEMP_ATTRS)
    tank(hass, 54.9, 49.6)                                               # only a little cooling
    await hass.async_block_till_done()
    await minutes(hass, freezer, 3, heat_pump_reports=True)
    assert entry.runtime_data.ledger.log == [] and entry.runtime_data.ledger.other == []


async def test_a_stale_reading_is_not_a_bath_filled_in_seconds(hass: HomeAssistant, entry, freezer) -> None:
    """Right after a restart the tank sensors still show the old values; seconds later the real, much lower ones
    arrive. No bath fills that fast: the hot water went before, and counts once."""
    freezer.tick(timedelta(seconds=20))
    tank(hass, 54.8, 30.0)
    await hass.async_block_till_done()
    assert state(hass, SHOWERING) == "off"
    await wait(hass, freezer, 2)
    m = entry.runtime_data
    assert len(m.ledger.log) == 1 and m.ledger.log[0]["estimated"] is True
    assert m.ledger.other == []


async def test_a_sensor_that_was_unavailable(hass: HomeAssistant, entry, freezer) -> None:
    """The bottom sensor is unavailable for a while (a heat pump reporting -1 °C counts too); a little hot water
    went meanwhile: a tap, logged once."""
    freezer.tick(timedelta(minutes=2))
    hass.states.async_set("sensor.tank_bottom", "unavailable", {})
    await hass.async_block_till_done()
    freezer.tick(timedelta(minutes=30))
    async_fire_time_changed(hass)
    tank(hass, 55.0, 48.5)
    await hass.async_block_till_done()
    await wait(hass, freezer, 2)
    m = entry.runtime_data
    assert m.ledger.log == []
    assert [o["kind"] for o in m.ledger.other] == ["tap"] and m.ledger.other[0]["estimated"] is True


async def test_a_shower_after_a_quiet_night_is_followed_live(hass: HomeAssistant, deps, freezer) -> None:
    """At night a heat pump can be quiet for 20 minutes. The first reading of a shower after that is no gap."""
    heat_pump_device(hass)
    entry = await setup_entry(hass)
    await minutes(hass, freezer, 30, heat_pump_reports=False)
    for bottom in (47.5, 45.0, 42.5, 40.0, 37.5):
        hass.states.async_set(OUTSIDE, f"{11.0 + bottom / 100:.2f}", TEMP_ATTRS)
        await step(hass, freezer, 2, 55.0, bottom)
    assert state(hass, SHOWERING) == "on"
    await wait(hass, freezer, 6)
    last = entry.runtime_data.ledger.log[-1]
    assert last["kind"] == "shower" and "estimated" not in last
