"""Live behaviour: the tank sensors change over time and the integration follows a shower, washing up, heat that
leaves without water, and the bathroom signal."""
from __future__ import annotations

from datetime import timedelta

from homeassistant.core import HomeAssistant
from pytest_homeassistant_custom_component.common import async_fire_time_changed

from custom_components.shower_time import model

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
    assert entry.runtime_data.ledger.totals["showers"] == 1
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


async def test_a_bath_counts_as_a_session_even_with_the_bathroom_signal(hass: HomeAssistant, deps, freezer) -> None:
    """The bathroom signal makes a bath look like a shower at first; its pace (far above a shower's) makes it a
    fill while it runs, and once done a session of its own: counted like a shower, in shower minutes of heat."""
    hass.states.async_set(HUMIDITY, "60", {"unit_of_measurement": "%", "device_class": "humidity"})
    entry = await setup_entry(hass, {"bathroom_sensor": HUMIDITY})
    m = entry.runtime_data
    per_minute = m.estimate.per_minute
    await start_reading(hass, freezer)
    freezer.tick(timedelta(minutes=1))
    hass.states.async_set(HUMIDITY, "66", {"unit_of_measurement": "%", "device_class": "humidity"})
    await hass.async_block_till_done()
    bottom = 50.0
    for _ in range(6):
        bottom -= 4.5 * per_minute / 0.84                    # about three times a shower's pace in this tank
        await step(hass, freezer, 1, 55.0, bottom)
    assert hass.states.get(IN_USE).attributes["kind"] == "fill"
    assert state(hass, SHOWERING) == "off"
    await wait(hass, freezer, 6)
    assert state(hass, "sensor.hot_water_showers_today") == "1"
    last = m.ledger.log[-1]
    assert last["kind"] == "bath" and last["minutes"] > last["duration"]   # more heat than its minutes of running
    assert float(state(hass, "sensor.hot_water_other_hot_water_today")) == 0
    assert hass.states.get(EVENT).attributes["event_type"] == "shower_ended"
    assert hass.states.get(EVENT).attributes["kind"] == "bath"
    split = m.since_reheat()
    if split is not None:                                      # the bath is not cooling
        assert split["showered_minutes"] > 0 and split["other_minutes"] == 0


async def test_a_bucket_stays_other_hot_water(hass: HomeAssistant, entry, freezer) -> None:
    """Filling a bucket with hot water: fast, but too little heat for a bath."""
    await start_reading(hass, freezer)
    await step(hass, freezer, 1, 55.0, 42.0)                 # 4.3 K in a minute: a fill, 5 shower minutes of heat
    assert hass.states.get(IN_USE).attributes["kind"] == "fill"
    await wait(hass, freezer, 6)
    assert state(hass, "sensor.hot_water_showers_today") == "0"
    assert hass.states.get("sensor.hot_water_other_hot_water_today").attributes["by_kind"] == {"fill": 1}


async def _short_use(hass: HomeAssistant, freezer) -> None:
    """Hot water for four minutes, about 1.4 K off the tank average: below the shower threshold."""
    await start_reading(hass, freezer)
    await step(hass, freezer, 2, 55.0, 48.7)
    await step(hass, freezer, 2, 55.0, 47.5)
    await wait(hass, freezer, 6)


async def test_a_short_shower_at_the_usual_time_counts(hass: HomeAssistant, entry, freezer) -> None:
    """Short showers that stay below the threshold count when they come at the time this household showers."""
    from homeassistant.util import dt as dt_util
    m = entry.runtime_data
    now = dt_util.now()
    for days, shift in ((1, -20), (2, 5), (3, 25)):                     # showers around this time on earlier days
        t = (now - timedelta(days=days, minutes=-shift)).isoformat(timespec="minutes")
        m.ledger.log.append({"start": t, "minutes": 8.0, "heat_kwh": 1.0, "electricity_kwh": 0.4, "cost": None,
                             "source": "live", "kind": "shower"})
    await _short_use(hass, freezer)
    assert state(hass, "sensor.hot_water_showers_today") == "1"
    assert m.ledger.other == []


def _shower_habit(m) -> None:
    """Showers around this time of day on the three days before."""
    from homeassistant.util import dt as dt_util
    now = dt_util.now()
    for days, shift in ((1, -20), (2, 5), (3, 25)):
        t = (now - timedelta(days=days, minutes=-shift)).isoformat(timespec="minutes")
        m.ledger.log.append({"start": t, "minutes": 8.0, "heat_kwh": 1.0, "electricity_kwh": 0.4, "cost": None,
                             "source": "live", "kind": "shower"})


async def test_hand_washing_at_the_usual_time_is_a_tap_when_the_bathroom_stays_quiet(hass: HomeAssistant, deps,
                                                                                       freezer) -> None:
    """#10: with a bathroom signal, a short use at the hour this household showers is only a shower when that
    signal saw the bathroom in use."""
    hass.states.async_set(HUMIDITY, "60", {"unit_of_measurement": "%", "device_class": "humidity"})
    entry = await setup_entry(hass, {"bathroom_sensor": HUMIDITY})
    m = entry.runtime_data
    _shower_habit(m)
    await _short_use(hass, freezer)
    assert state(hass, "sensor.hot_water_showers_today") == "0"
    assert [o["kind"] for o in m.ledger.other] == ["tap"]


async def test_a_short_shower_with_the_bathroom_signal_still_counts(hass: HomeAssistant, deps, freezer) -> None:
    """The humidity rises a little after the water stopped: still a shower."""
    hass.states.async_set(HUMIDITY, "60", {"unit_of_measurement": "%", "device_class": "humidity"})
    entry = await setup_entry(hass, {"bathroom_sensor": HUMIDITY})
    m = entry.runtime_data
    _shower_habit(m)
    await start_reading(hass, freezer)
    await step(hass, freezer, 2, 55.0, 48.7)
    await step(hass, freezer, 2, 55.0, 47.5)
    await wait(hass, freezer, 1)
    hass.states.async_set(HUMIDITY, "66", {"unit_of_measurement": "%", "device_class": "humidity"})
    await hass.async_block_till_done()
    await wait(hass, freezer, 6)
    assert state(hass, "sensor.hot_water_showers_today") == "1"
    assert m.ledger.other == []


async def test_without_a_bath_a_big_fill_stays_other_hot_water(hass: HomeAssistant, deps, freezer) -> None:
    """No bath on this tank: hot water drawn at a bath's pace is a bucket or sink, never a bath session."""
    entry = await setup_entry(hass, {"bathtub": False})
    m = entry.runtime_data
    per_minute = m.estimate.per_minute
    await start_reading(hass, freezer)
    bottom = 50.0
    for _ in range(6):
        bottom -= 4.5 * per_minute / 0.84
        await step(hass, freezer, 1, 55.0, bottom)
    assert hass.states.get(IN_USE).attributes["kind"] == "fill"
    await wait(hass, freezer, 6)
    assert state(hass, "sensor.hot_water_showers_today") == "0"
    assert not any(x.get("kind") == "bath" for x in m.ledger.log)
    assert [o["kind"] for o in m.ledger.other] == ["fill"]


async def test_the_same_short_use_at_another_time_is_a_tap(hass: HomeAssistant, entry, freezer) -> None:
    await _short_use(hass, freezer)
    assert state(hass, "sensor.hot_water_showers_today") == "0"
    assert [o["kind"] for o in entry.runtime_data.ledger.other] == ["tap"]


def _bottom_for(m, avg: float) -> float:
    """The bottom reading (top 55 °C) at which the tank average is ``avg``."""
    lo, hi = 0.0, 55.0
    for _ in range(40):
        mid = (lo + hi) / 2
        lo, hi = (mid, hi) if model.tank_average(m.params, 55.0, mid) < avg else (lo, mid)
    return round(hi, 2)


async def _slow_use(hass: HomeAssistant, freezer, m) -> None:
    """Washing up: ten minutes of hot water, 2.2 K off the tank average — more than the shower threshold, but at a
    fraction of a shower's pace (2.2 K in ten minutes against 7-11 K for a shower), with a warm bottom."""
    await start_reading(hass, freezer)
    avg = model.tank_average(m.params, 55.0, 50.0)
    for _ in range(5):
        avg -= 0.44
        await step(hass, freezer, 2, 55.0, _bottom_for(m, avg))
    await wait(hass, freezer, 6)


async def test_washing_up_with_a_shower_s_heat_but_not_its_pace_is_a_tap(hass: HomeAssistant, entry, freezer) -> None:
    """#10: washing up that took as much heat as the shower threshold, over ten minutes."""
    m = entry.runtime_data
    await _slow_use(hass, freezer, m)
    assert state(hass, "sensor.hot_water_showers_today") == "0"
    assert not any(e["source"] == "live" for e in m.ledger.log) and [o["kind"] for o in m.ledger.other] == ["tap"]
    last = m.ledger.other[-1]
    assert last["rule"] == "slow" and last["drawn"] >= 1.8 and last["seen"] == []
    assert m.use_stats["tap"] == [] and m.use_stats["shower"] == []    # says nothing about a tap's heat


async def test_a_slow_use_the_bathroom_signal_saw_is_a_shower(hass: HomeAssistant, deps, freezer) -> None:
    hass.states.async_set(HUMIDITY, "60", {"unit_of_measurement": "%", "device_class": "humidity"})
    entry = await setup_entry(hass, {"bathroom_sensor": HUMIDITY})
    m = entry.runtime_data
    freezer.tick(timedelta(minutes=1))
    hass.states.async_set(HUMIDITY, "66", {"unit_of_measurement": "%", "device_class": "humidity"})
    await hass.async_block_till_done()
    await _slow_use(hass, freezer, m)
    assert state(hass, "sensor.hot_water_showers_today") == "1"
    assert m.ledger.log[-1]["seen"] == ["bathroom"] and "rule" not in m.ledger.log[-1]
