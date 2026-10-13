"""Live behaviour: the tank sensors change over time and the integration follows a shower, washing up, heat that
leaves without water, and the bathroom signal."""
from __future__ import annotations

from datetime import timedelta

from homeassistant.core import HomeAssistant
from pytest_homeassistant_custom_component.common import async_fire_time_changed

from .conftest import HUMIDITY, setup_entry, tank

IN_USE = "binary_sensor.hot_water_hot_water_in_use"
SHOWERING = "binary_sensor.hot_water_showering"
EVENT = "event.hot_water_shower"


def state(hass: HomeAssistant, entity_id: str) -> str:
    return hass.states.get(entity_id).state


async def step(hass: HomeAssistant, freezer, minutes: float, top: float, bottom: float) -> None:
    freezer.tick(timedelta(minutes=minutes))
    tank(hass, top, bottom)
    async_fire_time_changed(hass)
    await hass.async_block_till_done()


async def wait(hass: HomeAssistant, freezer, minutes: float) -> None:
    """Let time pass without new readings (the integration ends a use 5 minutes after the last fall)."""
    for _ in range(int(minutes * 4)):
        freezer.tick(timedelta(seconds=15))
        async_fire_time_changed(hass)
        await hass.async_block_till_done()


async def start_reading(hass: HomeAssistant, freezer) -> None:
    """A fresh, slightly higher bottom reading so the test controls the time of the reading before a use."""
    await step(hass, freezer, 1, 55.0, 50.0)


async def test_a_shower_is_followed_and_logged(hass: HomeAssistant, entry, freezer) -> None:
    await start_reading(hass, freezer)
    minutes_before = float(state(hass, "sensor.hot_water_shower_time"))

    await step(hass, freezer, 2, 55.0, 47.0)                 # cold water comes in at the bottom
    assert state(hass, IN_USE) == "on"
    assert hass.states.get(IN_USE).attributes["kind"] == "unknown"     # hot water, not yet known to be a shower
    assert state(hass, SHOWERING) == "off"

    await step(hass, freezer, 2, 55.0, 44.0)                 # enough heat at a shower's pace: a shower
    assert state(hass, SHOWERING) == "on"
    assert hass.states.get(EVENT).attributes["event_type"] == "shower_started"
    assert float(state(hass, "sensor.hot_water_current_shower")) >= 4
    for bottom in (41.0, 38.0, 35.0):
        await step(hass, freezer, 2, 55.0, bottom)
    assert float(state(hass, "sensor.hot_water_shower_time")) < minutes_before

    await wait(hass, freezer, 6)                             # the water is off
    assert state(hass, IN_USE) == "off" and state(hass, SHOWERING) == "off"
    assert state(hass, "sensor.hot_water_last_shower") == "10"
    assert state(hass, "sensor.hot_water_showers_today") == "1"
    assert state(hass, "sensor.hot_water_showers_total") == "1"
    ended = hass.states.get(EVENT).attributes
    assert ended["event_type"] == "shower_ended" and ended["minutes"] == 10.0
    assert float(state(hass, "sensor.hot_water_other_hot_water_today")) == 0


async def test_washing_up_is_not_a_shower(hass: HomeAssistant, entry, freezer) -> None:
    await start_reading(hass, freezer)
    await step(hass, freezer, 1, 55.0, 49.6)                 # a sink being filled: a little heat
    assert state(hass, IN_USE) == "on" and state(hass, SHOWERING) == "off"
    await wait(hass, freezer, 6)
    assert state(hass, IN_USE) == "off"
    assert state(hass, "sensor.hot_water_showers_today") == "0"
    other = hass.states.get("sensor.hot_water_other_hot_water_today")
    assert float(other.state) > 0 and other.attributes["by_kind"] == {"tap": 1}
    assert hass.states.get(EVENT).attributes["event_type"] == "hot_water_used"


async def test_heat_leaving_without_water_is_ignored(hass: HomeAssistant, entry, freezer) -> None:
    """A heat pump starting a reheat: the top dips, the bottom hardly moves. No water was drawn."""
    await start_reading(hass, freezer)
    await step(hass, freezer, 2, 53.0, 49.9)
    await step(hass, freezer, 2, 51.0, 49.8)
    assert hass.states.get(IN_USE).state == "off"            # recognised: not hot water use
    await wait(hass, freezer, 6)
    assert state(hass, "sensor.hot_water_showers_today") == "0"
    assert float(state(hass, "sensor.hot_water_other_hot_water_today")) == 0
    assert entry.runtime_data.coil_events == 1


async def test_bathroom_signal_recognises_a_shower_at_once(hass: HomeAssistant, deps, freezer) -> None:
    hass.states.async_set(HUMIDITY, "60", {"unit_of_measurement": "%", "device_class": "humidity"})
    entry = await setup_entry(hass, {"bathroom_sensor": HUMIDITY})
    assert entry.runtime_data.bath_signal.kind == "humidity"
    await start_reading(hass, freezer)
    freezer.tick(timedelta(minutes=1))
    hass.states.async_set(HUMIDITY, "66", {"unit_of_measurement": "%", "device_class": "humidity"})
    await hass.async_block_till_done()
    await step(hass, freezer, 1, 55.0, 47.0)                 # the first reading already says: shower
    assert state(hass, SHOWERING) == "on"
    started = hass.states.get(EVENT).attributes
    assert started["event_type"] == "shower_started" and started["bathroom_signal"] is True
