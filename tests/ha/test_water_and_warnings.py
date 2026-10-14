"""The water meter followed live, the warnings for the card, and days with Home Assistant off."""
from __future__ import annotations

from datetime import timedelta

from homeassistant.core import HomeAssistant
from homeassistant.util import dt as dt_util
from pytest_homeassistant_custom_component.common import async_fire_time_changed

from .conftest import setup_entry, tank
from .test_live import SHOWERING, state, wait

WATER = "sensor.water_meter_total"
WATER_ATTRS = {"unit_of_measurement": "L", "device_class": "water", "state_class": "total_increasing"}


async def minute(hass: HomeAssistant, freezer, water: float | None, bottom: float | None) -> None:
    freezer.tick(timedelta(minutes=1))
    if water is not None:
        hass.states.async_set(WATER, str(water), WATER_ATTRS)
    if bottom is not None:
        tank(hass, 55.0, bottom)
    async_fire_time_changed(hass)
    await hass.async_block_till_done()


async def test_water_meter_confirms_early_and_times_the_shower(hass: HomeAssistant, deps, freezer) -> None:
    hass.states.async_set(WATER, "1000", WATER_ATTRS)
    entry = await setup_entry(hass, {"water_sensor": WATER})
    assert entry.runtime_data.water_signal.kind == "total"
    freezer.tick(timedelta(minutes=30))                       # the meter has been still for a while
    tank(hass, 55.0, 50.0)
    await hass.async_block_till_done()

    await minute(hass, freezer, 1001, 49.6)                   # m1: water starts, the bottom begins to fall
    assert state(hass, SHOWERING) == "off"
    await minute(hass, freezer, 1009, 49.0)                   # m2: 8 L/min
    assert state(hass, SHOWERING) == "off"                    # 0.6 K not yet left the tank
    await minute(hass, freezer, 1017, 48.4)                   # m3: two minutes of steady flow and heat leaving
    assert state(hass, SHOWERING) == "on"                     # by heat alone this takes about twice as long
    assert hass.states.get("event.hot_water_shower").attributes["water_meter"] is True

    litres, bottom = 1017, 48.4
    for _ in range(5):                                        # m4..m8: the tank keeps falling
        litres, bottom = litres + 8, bottom - 0.6
        await minute(hass, freezer, litres, bottom)
    for _ in range(2):                                        # m9, m10: water still runs, the tank shows no change
        litres += 8
        await minute(hass, freezer, litres, None)
    await wait(hass, freezer, 7)
    assert state(hass, SHOWERING) == "off"
    assert state(hass, "sensor.hot_water_last_shower") == "10"   # from m0 until the meter stopped (m10)


async def test_cold_water_alone_is_not_a_shower(hass: HomeAssistant, deps, freezer) -> None:
    """A toilet or washing machine: water flows, but no heat leaves the tank."""
    hass.states.async_set(WATER, "2000", WATER_ATTRS)
    await setup_entry(hass, {"water_sensor": WATER})
    freezer.tick(timedelta(minutes=30))
    for i in range(1, 6):
        await minute(hass, freezer, 2000 + 8 * i, None)
    assert state(hass, "binary_sensor.hot_water_hot_water_in_use") == "off"
    assert state(hass, SHOWERING) == "off"


async def test_warnings_reach_the_card(hass: HomeAssistant, entry) -> None:
    m = entry.runtime_data
    m.legionella_checked, m.legionella_at = True, (dt_util.now() - timedelta(days=12)).isoformat(timespec="minutes")
    m.loss = {**m.loss, "leak_like": True}
    tank(hass, 55.0, 49.7)
    await hass.async_block_till_done()
    problems = hass.states.get("sensor.hot_water_shower_time").attributes["problems"]
    assert {"key": "leak"} in problems
    assert {"key": "legionella", "days": 12} in problems


async def test_days_with_home_assistant_off_are_left_out_of_the_habits(hass: HomeAssistant, entry) -> None:
    m = entry.runtime_data
    m.alive_at = (dt_util.utcnow() - timedelta(days=2)).isoformat()
    m._note_downtime()
    skipped = m.habits.get("skip", [])
    assert len(skipped) == 3 and skipped[-1] == dt_util.now().date().isoformat()
    m.ledger.covered_from = (dt_util.now() - timedelta(days=5)).date().isoformat()
    m.habits = {**m.habits, "days": 0, "last_day": None}
    m._update_habits()
    assert m.habits["days"] == 3                               # 5 complete days back, 2 of them skipped


async def test_reheats_are_learned_per_day(hass: HomeAssistant, entry) -> None:
    m = entry.runtime_data
    today = dt_util.now().date()
    m.reheat_since = (today - timedelta(days=8)).isoformat()
    for d in range(1, 9):                                     # a reheat at 13:00 on each of the last 8 days
        m.reheat_log[(today - timedelta(days=d)).isoformat()] = [13]
    m._update_reheat_habits()
    r = m.habits["reheat"]
    assert r["days"] == 8 and r["hours"][13] > 0.9 and r["last_day"] == (today - timedelta(days=1)).isoformat()
    m._log_reheat(dt_util.utcnow())                           # live: a reheat starts now
    m._log_reheat(dt_util.utcnow() + timedelta(minutes=20))   # the same reheat resuming: not a second one
    assert m.reheat_log[today.isoformat()] == [dt_util.now().hour]


async def test_a_top_up_keeps_since_reheat_and_a_full_reheat_starts_it_again(hass: HomeAssistant, entry, freezer) -> None:
    """A reheat to a lower temperature in between is a top-up: "since reheat" goes on and notes when and how
    much. A reheat that brings the tank back to (about) full starts it again."""
    m = entry.runtime_data
    m._set_ref(m.estimate.minutes)
    since, ref = m.full_ref_at, m.ref_minutes()

    def reheat(low: float, high: float, top: float, bottom: float) -> None:
        for v in (low, low + 1, low + 4, high):          # the minutes fall, then rise: a reheat (no reheat entity)
            m._track_full(v)
        freezer.tick(timedelta(minutes=21))              # the rise stopped 20 minutes ago: the reheat is over
        m.top_value, m.bottom_value = top, bottom
        m._track_full(high)

    freezer.tick(timedelta(hours=1))
    reheat(0.3 * ref, 0.6 * ref, 45.0, 44.0)             # even, but only to 45 °C
    assert m.full_ref_at == since and len(m.top_ups) == 1
    assert m.top_ups[0]["added_minutes"] == round(0.6 * ref - (0.3 * ref + 1), 1) and m.top_ups[0]["top"] == 45.0
    split = m.since_reheat()
    assert split["since"] == since and split["last_top_up_at"] == m.top_ups[0]["at"]

    freezer.tick(timedelta(hours=1))
    reheat(0.5 * ref, 0.95 * ref, 55.0, 54.0)            # back to full
    assert m.full_ref_at != since and m.top_ups == []
    assert m.since_reheat()["last_top_up_at"] is None
