"""The water meter followed live, the warnings for the card, and days with Home Assistant off."""
from __future__ import annotations

from datetime import timedelta

from homeassistant.core import HomeAssistant
from homeassistant.util import dt as dt_util
from pytest_homeassistant_custom_component.common import async_fire_time_changed

from .conftest import HUMIDITY, setup_entry, tank
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
    for _ in range(5):                                        # m4..m8: the bottom falls at a shower's pace
        litres, bottom = litres + 8, bottom - 2.0
        await minute(hass, freezer, litres, bottom)
    for _ in range(2):                                        # m9, m10: water still runs, the tank shows no change
        litres += 8
        await minute(hass, freezer, litres, None)
    await wait(hass, freezer, 7)
    assert state(hass, SHOWERING) == "off"
    assert state(hass, "sensor.hot_water_last_shower") == "10"   # from m0 until the meter stopped (m10)


async def test_with_a_bathroom_signal_a_steady_flow_alone_does_not_confirm(hass: HomeAssistant, deps, freezer) -> None:
    """#10: a kitchen tap can run as steadily as a shower. With a bathroom signal set up only that signal lowers
    the threshold: a steady flow while the bathroom stays quiet needs a shower's heat."""
    hass.states.async_set(WATER, "1000", WATER_ATTRS)
    hass.states.async_set(HUMIDITY, "60", {"unit_of_measurement": "%", "device_class": "humidity"})
    entry = await setup_entry(hass, {"water_sensor": WATER, "bathroom_sensor": HUMIDITY})
    freezer.tick(timedelta(minutes=30))
    tank(hass, 55.0, 50.0)
    await hass.async_block_till_done()
    litres = 1001
    await minute(hass, freezer, litres, 49.6)
    for bottom in (49.0, 48.4, 47.8):                         # steady 8 L/min, 1.3 K off the tank average
        litres += 8
        await minute(hass, freezer, litres, bottom)
    assert state(hass, SHOWERING) == "off"                    # with the water meter alone: on since m3
    await wait(hass, freezer, 7)
    assert state(hass, "sensor.hot_water_showers_today") == "0"
    other = entry.runtime_data.ledger.other
    assert [o["kind"] for o in other] == ["tap"] and other[-1]["seen"] == ["water_meter"]


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


async def test_no_reheated_without_a_reheat(hass: HomeAssistant, entry, freezer) -> None:
    """#10: uses logged longer than the tank lost showed up as 'reheated' while nothing reheated. Without a seen
    reheat they are cut back to what the tank lost; with a top-up the surplus is what it added."""
    m = entry.runtime_data
    m._set_ref(m.estimate.minutes)
    freezer.tick(timedelta(hours=1))
    m.ledger.log.append({"start": dt_util.now().isoformat(timespec="minutes"), "minutes": 8.0, "heat_kwh": 1.0,
                         "electricity_kwh": 0.4, "cost": None, "source": "live", "kind": "shower"})
    split = m.since_reheat()
    assert split["reheated_minutes"] == 0 and split["overcounted_minutes"] > 7
    assert split["showered_minutes"] < 1 and split["total_minutes"] == split["reference_minutes"]
    m.top_ups = [{"at": dt_util.now().isoformat(timespec="minutes"), "until": None, "added_minutes": 8.0, "top": 55.0}]
    split = m.since_reheat()
    assert split["reheated_minutes"] > 7 and split["showered_minutes"] == 8.0 and split["overcounted_minutes"] == 0


async def test_full_again_follows_the_pace_of_this_reheat(hass: HomeAssistant, entry, freezer) -> None:
    """A steady reheat that runs faster than the learned average: after 20 minutes its own pace counts."""
    from types import SimpleNamespace
    m = entry.runtime_data
    m.params["reheat_rate"] = {"v": 0.2, "n": 10}
    m.params["full_avg"] = 54.0
    est = lambda avg: SimpleNamespace(average=avg, empty_at=30.0, per_minute=0.6)
    avg = 34.0
    m.ledger._forecast(m, est(avg), True)                     # the reheat starts: only the learned pace is known
    first = (m.ledger.eta_full - dt_util.now()).total_seconds() / 60
    assert abs(first - (54.0 - avg) / 0.2) < 0.5
    for _ in range(20):                                       # 0.4 K/min, twice the learned pace
        freezer.tick(timedelta(minutes=1))
        avg += 0.4
        m.ledger._forecast(m, est(avg), True)
    left = (m.ledger.eta_full - dt_util.now()).total_seconds() / 60
    assert abs(left - (54.0 - avg) / 0.4) < 0.5
    m.ledger._forecast(m, est(avg), False)                    # done: no forecast, the next reheat starts fresh
    assert m.ledger.eta_full is None and m.ledger._heat_start is None


async def test_a_cop_filled_in_is_used_and_the_old_default_means_learn(hass: HomeAssistant, deps) -> None:
    entry = await setup_entry(hass, {"cop": 3.0})
    m = entry.runtime_data
    m.ledger.cop_learned = 2.4
    assert m.ledger.cop_heat_pump(m) == (3.0, "manual")
    assert m.ledger.cop(m) == round(3.0 / m.params["veff_factor"]["v"], 2)
    assert hass.states.get("sensor.hot_water_shower_time").attributes["cop_source"] in ("manual", "default")
    entry2 = await setup_entry_named(hass, {"cop": 2.5})
    m2 = entry2.runtime_data
    m2.ledger.cop_learned = 2.4
    assert m2.ledger.cop_heat_pump(m2) == (2.4, "learned")


async def setup_entry_named(hass: HomeAssistant, options: dict):
    from pytest_homeassistant_custom_component.common import MockConfigEntry
    hass.states.async_set("sensor.other_top", "55", {"unit_of_measurement": "°C", "device_class": "temperature"})
    e = MockConfigEntry(domain="shower_time", title="Other", unique_id="sensor.other_top",
                        data={"top_sensor": "sensor.other_top", "tank_volume": 150, "temperature_mode": "auto",
                              "profile": "generic", "flow_source": "unknown"}, options=options)
    e.add_to_hass(hass)
    assert await hass.config_entries.async_setup(e.entry_id)
    await hass.async_block_till_done()
    return e


FLOW = "sensor.water_flow"
FLOW_ATTRS = {"unit_of_measurement": "L/min", "device_class": "volume_flow_rate"}
HUMIDITY_ATTRS = {"unit_of_measurement": "%", "device_class": "humidity"}


async def flow_minute(hass: HomeAssistant, freezer, flow: float, bottom: float | None) -> None:
    freezer.tick(timedelta(minutes=1))
    hass.states.async_set(FLOW, str(flow), FLOW_ATTRS)
    if bottom is not None:
        tank(hass, 55.0, bottom)
    async_fire_time_changed(hass)
    await hass.async_block_till_done()


async def test_signals_that_load_after_startup_are_still_followed(hass: HomeAssistant, deps) -> None:
    """#10: at startup a water meter or humidity sensor may not be loaded yet, or unavailable without its unit.
    Their kind comes from the first real state, not from a guess at setup (a humidity sensor taken for a fan)."""
    hass.states.async_set(HUMIDITY, "unavailable", {})
    entry = await setup_entry(hass, {"water_sensor": FLOW, "bathroom_sensor": HUMIDITY})
    m = entry.runtime_data
    assert m.water_signal is None and m.bath_signal is None
    hass.states.async_set(FLOW, "0", FLOW_ATTRS)
    hass.states.async_set(HUMIDITY, "60", HUMIDITY_ATTRS)
    await hass.async_block_till_done()
    assert m.water_signal.kind == "rate" and m.bath_signal.kind == "humidity"


async def test_washing_up_in_bursts_after_a_shower_is_no_shower(hass: HomeAssistant, deps, freezer) -> None:
    """#10: the dishes right after a shower. The bathroom is still humid from the shower, the tank loses a shower's
    heat, but the water meter only saw short bursts: a tap, not a second shower."""
    hass.states.async_set(FLOW, "0", FLOW_ATTRS)
    hass.states.async_set(HUMIDITY, "60", HUMIDITY_ATTRS)
    entry = await setup_entry(hass, {"water_sensor": FLOW, "bathroom_sensor": HUMIDITY})
    freezer.tick(timedelta(minutes=30))
    tank(hass, 55.0, 50.0)
    await hass.async_block_till_done()
    for humidity in ("66", "69"):                             # the shower before: humid since 12 minutes, still rising
        freezer.tick(timedelta(minutes=4 if humidity == "69" else 0))
        hass.states.async_set(HUMIDITY, humidity, HUMIDITY_ATTRS)
        await hass.async_block_till_done()
    freezer.tick(timedelta(minutes=8))
    tank(hass, 55.0, 50.0)
    await hass.async_block_till_done()
    bottom = 50.0
    for flow in (5.0, 0.0, 6.0, 0.0, 5.0, 0.0, 6.0, 0.0):    # the tap on and off
        bottom -= 1.2
        await flow_minute(hass, freezer, flow, bottom)
        # the humidity of the shower before does not make it a shower while it runs either
        assert entry.runtime_data.use_kind() != "shower"
    await wait(hass, freezer, 7)
    assert state(hass, "sensor.hot_water_showers_today") == "0"
    other = entry.runtime_data.ledger.other
    assert other[-1]["kind"] == "tap" and other[-1]["drawn"] >= 1.8


async def test_a_metered_shower_lasts_as_long_as_the_water_ran(hass: HomeAssistant, deps, freezer) -> None:
    """#10: the tank reports minutes late, so the use it sees runs on after the water stopped. With a water meter
    the shower lasts as long as the water ran."""
    hass.states.async_set(FLOW, "0", FLOW_ATTRS)
    await setup_entry(hass, {"water_sensor": FLOW})
    freezer.tick(timedelta(minutes=30))
    tank(hass, 55.0, 50.0)
    await hass.async_block_till_done()
    bottom = 50.0
    for i in range(9):                                        # 6 minutes of water, the tank falls for 9
        bottom -= 2.0
        await flow_minute(hass, freezer, 7.0 if i < 6 else 0.0, bottom)
    await wait(hass, freezer, 7)
    assert state(hass, "sensor.hot_water_showers_today") == "1"
    assert state(hass, "sensor.hot_water_last_shower") == "6"


async def test_a_tap_after_a_metered_shower_is_a_tap_and_the_shower_stops_counting(hass: HomeAssistant, deps,
                                                                                   freezer) -> None:
    """#10: 8 minutes of shower, then the sink: the tank falls for minutes after the water stopped, so it is all one
    use. The live minutes stop when the meter saw the shower stop; the sink is logged as a tap with its share of
    the heat, by the litres."""
    hass.states.async_set(FLOW, "0", FLOW_ATTRS)
    entry = await setup_entry(hass, {"water_sensor": FLOW})
    m = entry.runtime_data
    freezer.tick(timedelta(minutes=30))
    tank(hass, 55.0, 50.0)
    await hass.async_block_till_done()
    bottom = 50.0
    for _ in range(8):                                        # m1..m8: the shower, 6.6 L/min
        bottom -= 2.0
        await flow_minute(hass, freezer, 6.6, bottom)
    for _ in range(3):                                        # m9..m11: the water is off, the tank still falls
        bottom -= 0.5
        await flow_minute(hass, freezer, 0.0, bottom)
    assert state(hass, "binary_sensor.hot_water_hot_water_in_use") == "on"   # the tank still falls
    assert state(hass, "binary_sensor.hot_water_showering") == "off"         # but nobody showers any more
    assert m.shower_stopped_at() is not None and m.use_kind() == "shower"
    live = m.ledger.running(m, dt_util.utcnow())["minutes"]
    assert live <= 9.5
    attrs = hass.states.get("sensor.hot_water_shower_time").attributes
    assert attrs["hot_water_use"]["kind"] == "unknown"                       # "hot water", no longer "showering"
    for flow in (4.2, 0.0, 2.8, 0.0):                         # m12..m15: the sink
        bottom -= 0.5
        await flow_minute(hass, freezer, flow, bottom)
    assert m.ledger.running(m, dt_util.utcnow())["minutes"] == live          # a short burst does not restart it
    await wait(hass, freezer, 7)
    assert state(hass, "sensor.hot_water_showers_today") == "1"
    assert state(hass, "sensor.hot_water_last_shower") == "8"
    shower, tap = m.ledger.log[-1], m.ledger.other[-1]
    assert tap["kind"] == "tap" and tap["rule"] == "tail" and tap["minutes"] == 3.0
    assert abs(tap["drawn"] / (shower["drawn"] + tap["drawn"]) - 7.0 / 59.8) < 0.01   # 7 of 59.8 litres


async def flow_and_tank(hass: HomeAssistant, freezer, flow: float, reading: tuple[float, float] | None) -> None:
    freezer.tick(timedelta(minutes=1))
    hass.states.async_set(FLOW, str(flow), FLOW_ATTRS)
    if reading is not None:
        tank(hass, *reading)
    async_fire_time_changed(hass)
    await hass.async_block_till_done()


# #10, 4 Nov 19:58: washing up after a bath, the tank nearly empty, read every 2 minutes; the meter per minute
LOW_TANK_WASHING_UP = [(2.71, None), (6.65, (58.0, 22.6)), (2.51, None), (5.5, (56.8, 21.0)), (5.09, None),
                       (4.82, (55.3, 20.1)), (4.12, None), (0.0, (53.9, 19.8)), (0.0, None), (2.6, (52.5, 19.5)),
                       (0.0, None), (3.35, (51.4, 19.3)), (0.0, None), (0.0, (50.3, 19.2)), (0.0, None),
                       (1.49, (49.7, 19.1)), (0.0, None)]


async def test_washing_up_in_a_nearly_empty_tank_is_a_tap(hass: HomeAssistant, deps, freezer) -> None:
    """#10: in a nearly empty tank the bottom is down near cold water and only the top falls. To the tank sensors
    that is heat leaving through the coil, and the heat and pace of a shower. The water meter saw the water (on
    and off, at most 4 minutes at a shower's flow) and the bathroom signal nothing: a tap with its heat, also at
    the hour this household showers. It was lost as "cooled"."""
    hass.states.async_set(FLOW, "0", FLOW_ATTRS)
    hass.states.async_set(HUMIDITY, "58", HUMIDITY_ATTRS)
    entry = await setup_entry(hass, {"water_sensor": FLOW, "bathroom_sensor": HUMIDITY}, top=59.4, bottom=23.7)
    m = entry.runtime_data
    for days in (1, 2):                                       # the evening shower at this time of day
        m.ledger.log.append({"start": (dt_util.now() - timedelta(days=days)).isoformat(timespec="minutes"),
                             "minutes": 10.0, "heat_kwh": 1.5, "electricity_kwh": 0.5, "cost": 0.15,
                             "source": "live", "kind": "shower"})
    freezer.tick(timedelta(minutes=30))
    tank(hass, 59.3, 23.6)
    await hass.async_block_till_done(wait_background_tasks=True)   # the startup's own reference is set first
    m._set_ref(m.estimate.minutes)
    left = m.estimate.minutes
    for i, (flow, reading) in enumerate(LOW_TANK_WASHING_UP):
        await flow_and_tank(hass, freezer, flow, reading)
        assert m.use_kind() != "shower"
        if i >= 3:                                            # the meter saw enough water: no coil after all
            assert m.use_kind() in (None, "unknown")
    await wait(hass, freezer, 7)
    assert m.coil_events == 0 and not m.showering
    assert state(hass, "sensor.hot_water_showers_today") == "0"
    tap = m.ledger.other[-1]
    assert tap["kind"] == "tap" and tap["rule"] == "unseen" and tap["drawn"] > 5
    split = m.since_reheat()
    assert split["other_minutes"] >= 0.9 * (left - split["remaining_minutes"]) and split["cooling_minutes"] < 0.5


async def test_a_shower_the_bathroom_signal_missed_counts_by_its_flow(hass: HomeAssistant, deps, freezer) -> None:
    """With a bathroom signal and a water meter, a shower the bathroom signal did not see still counts once the
    meter saw a shower's flow for longer than a kitchen tap runs."""
    hass.states.async_set(FLOW, "0", FLOW_ATTRS)
    hass.states.async_set(HUMIDITY, "58", HUMIDITY_ATTRS)
    entry = await setup_entry(hass, {"water_sensor": FLOW, "bathroom_sensor": HUMIDITY})
    m = entry.runtime_data
    freezer.tick(timedelta(minutes=30))
    tank(hass, 55.0, 50.0)
    await hass.async_block_till_done()
    bottom = 50.0
    for i in range(11):                                       # 8 minutes of 6.6 L/min, the tank falls for 11
        bottom -= 2.0 if i < 8 else 0.5
        await flow_and_tank(hass, freezer, 6.6 if i < 8 else 0.0, (55.0, bottom))
        if i == 3:
            assert m.use_kind() == "unknown"                  # 4 minutes: could still be the kitchen tap
    assert m.use_kind() == "shower"
    await wait(hass, freezer, 7)
    assert state(hass, "sensor.hot_water_showers_today") == "1"
    assert state(hass, "sensor.hot_water_last_shower") == "8"
