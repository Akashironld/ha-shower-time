"""A restart of Home Assistant in the middle of a hot water use must not lose it."""
from __future__ import annotations

from datetime import timedelta

from homeassistant.core import HomeAssistant

from .conftest import BOTTOM, TEMP_ATTRS, TOP, tank
from .test_live import SHOWERING, start_reading, state, step, wait


def LOG(m) -> list[dict]:
    """The shower log without what the first-run backfill read from the (test) recorder."""
    return [e for e in m.ledger.log if e.get("source") != "history"]


async def _shower_running(hass, freezer, entry) -> None:
    """A shower recognised and 10 minutes under way; the water has just stopped (not closed yet)."""
    entry.runtime_data._set_ref(entry.runtime_data.estimate.minutes)
    await start_reading(hass, freezer)
    await step(hass, freezer, 2, 55.0, 47.0)
    await step(hass, freezer, 2, 55.0, 44.0)
    for bottom in (41.0, 38.0, 35.0):
        await step(hass, freezer, 2, 55.0, bottom)
    assert state(hass, SHOWERING) == "on"


async def _restart(hass: HomeAssistant, freezer, entry, down: timedelta, top=None, bottom=None):
    assert await hass.config_entries.async_unload(entry.entry_id)
    freezer.tick(down)
    if top is not None:
        tank(hass, top, bottom)
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()
    return entry.runtime_data


async def test_a_shower_survives_a_short_restart(hass: HomeAssistant, entry, freezer) -> None:
    await _shower_running(hass, freezer, entry)
    cooling_before = entry.runtime_data.since_reheat()["cooling_minutes"]
    m = await _restart(hass, freezer, entry, timedelta(minutes=1))
    assert m.showering and m.use is not None and m.use.kind == "shower"     # carried on, not forgotten
    await wait(hass, freezer, 6)
    assert state(hass, SHOWERING) == "off"
    assert [e["kind"] for e in LOG(m)] == ["shower"] and m.ledger.totals["showers"] == 1
    logged = LOG(m)[0]
    assert 8 <= logged["minutes"] <= 13 and not logged.get("estimated")
    split = m.since_reheat()
    assert split["showered_minutes"] > 0
    assert split["cooling_minutes"] < cooling_before + 3         # the shower's heat is not booked as cooling


async def test_a_shower_that_ended_while_home_assistant_was_off_is_logged_whole(hass: HomeAssistant, entry, freezer) -> None:
    await _shower_running(hass, freezer, entry)
    m = await _restart(hass, freezer, entry, timedelta(minutes=8), 55.0, 35.0)     # back after 8 minutes, tank as left
    await wait(hass, freezer, 6)
    assert len(LOG(m)) == 1 and 8 <= LOG(m)[0]["minutes"] <= 13


async def test_a_long_downtime_closes_the_use_as_an_estimated_entry(hass: HomeAssistant, entry, freezer) -> None:
    await _shower_running(hass, freezer, entry)
    m = await _restart(hass, freezer, entry, timedelta(minutes=40), 55.0, 30.0)
    assert not m.showering                                           # not followed on after so long
    await wait(hass, freezer, 4)
    assert len(LOG(m)) == 1 and LOG(m)[0]["estimated"] is True and LOG(m)[0]["minutes"] > 3
    assert m.since_reheat()["showered_minutes"] > 0


async def test_a_use_not_yet_seen_when_home_assistant_stopped_is_judged_after_the_restart(hass: HomeAssistant, entry, freezer) -> None:
    await start_reading(hass, freezer)                               # quiet tank, nothing detected
    m = await _restart(hass, freezer, entry, timedelta(minutes=2), 55.0, 38.0)   # a shower while it was off
    await wait(hass, freezer, 4)
    assert len(LOG(m)) == 1 and LOG(m)[0]["estimated"] is True


async def test_a_restart_without_a_use_logs_nothing(hass: HomeAssistant, entry, freezer) -> None:
    await start_reading(hass, freezer)
    m = await _restart(hass, freezer, entry, timedelta(minutes=2), 54.9, 49.9)   # only standing loss
    await wait(hass, freezer, 4)
    assert LOG(m) == [] and m.ledger.other == [] and not m.showering
    m = await _restart(hass, freezer, entry, timedelta(seconds=20))               # and a plain reload
    await wait(hass, freezer, 4)
    assert LOG(m) == [] and m.ledger.other == [] and m.coil_events == 0


async def test_a_shower_that_was_closed_and_logged_is_not_continued_after_a_restart(hass: HomeAssistant, entry, freezer) -> None:
    await _shower_running(hass, freezer, entry)
    await wait(hass, freezer, 6)                                      # closed and logged
    m = entry.runtime_data
    assert len(LOG(m)) == 1 and not m.showering
    m = await _restart(hass, freezer, entry, timedelta(minutes=1))
    assert not m.showering and m.use is None
    await wait(hass, freezer, 6)
    assert len(LOG(m)) == 1 and m.ledger.totals["showers"] == 1 and m.ledger.other == []


async def _reconfigure(hass, entry, top, bottom):
    result = await entry.start_reconfigure_flow(hass)
    await hass.config_entries.flow.async_configure(result["flow_id"], {"top_sensor": top, "bottom_sensor": bottom})
    await hass.async_block_till_done(wait_background_tasks=True)
    return entry.runtime_data


async def test_other_sensors_after_reconfigure_are_not_compared_with_the_old_ones(hass: HomeAssistant, entry, freezer) -> None:
    await start_reading(hass, freezer)
    hass.states.async_set("sensor.new_top", "40", TEMP_ATTRS)               # reads far lower than the old tank
    hass.states.async_set("sensor.new_bottom", "20", TEMP_ATTRS)
    m = await _reconfigure(hass, entry, "sensor.new_top", "sensor.new_bottom")
    await wait(hass, freezer, 4)
    assert LOG(m) == [] and m.ledger.other == [] and not m.showering and m._gap is None


async def test_a_changed_bottom_sensor_is_not_compared_with_the_old_one(hass: HomeAssistant, entry, freezer) -> None:
    await start_reading(hass, freezer)
    hass.states.async_set("sensor.new_bottom", "20", TEMP_ATTRS)
    hass.config_entries.async_update_entry(entry, options={"bottom_sensor": "sensor.new_bottom"})
    await hass.async_block_till_done(wait_background_tasks=True)
    m = entry.runtime_data
    assert m.bottom == "sensor.new_bottom"
    await wait(hass, freezer, 4)
    assert LOG(m) == [] and m.ledger.other == [] and not m.showering


async def test_a_running_use_of_the_old_sensors_is_not_continued(hass: HomeAssistant, entry, freezer) -> None:
    await _shower_running(hass, freezer, entry)
    hass.states.async_set("sensor.new_top", "40", TEMP_ATTRS)
    hass.states.async_set("sensor.new_bottom", "20", TEMP_ATTRS)
    m = await _reconfigure(hass, entry, "sensor.new_top", "sensor.new_bottom")
    assert not m.showering and m.use is None
    await wait(hass, freezer, 6)
    assert LOG(m) == []


async def test_a_sensor_that_drops_out_mid_use_closes_it_as_before(hass: HomeAssistant, entry, freezer) -> None:
    await _shower_running(hass, freezer, entry)
    m = entry.runtime_data
    hass.states.async_set(TOP, "unavailable", TEMP_ATTRS)
    await hass.async_block_till_done()
    await wait(hass, freezer, 10)                                      # no estimate: nothing runs, as before
    tank(hass, 55.0, 35.0)                                             # back as it was: no further heat left
    await wait(hass, freezer, 6)
    assert not m.showering and m.use is None
    assert len(LOG(m)) == 1 and not LOG(m)[0].get("estimated") and m.ledger.other == []   # same as before this change


async def test_a_restored_use_whose_sensor_never_returns_is_dropped_and_booked_once_on_return(hass: HomeAssistant, entry, freezer) -> None:
    await _shower_running(hass, freezer, entry)
    assert await hass.config_entries.async_unload(entry.entry_id)
    freezer.tick(timedelta(minutes=1))
    hass.states.async_set(TOP, "unavailable", TEMP_ATTRS)
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()
    m = entry.runtime_data
    assert m.showering                                                  # held: the readings are not back yet
    await wait(hass, freezer, 1)
    assert m.showering
    await wait(hass, freezer, 4)                                        # past the hold
    assert not m.showering and m.use is None and LOG(m) == []
    tank(hass, 55.0, 30.0)                                              # the sensors come back, much lower
    await wait(hass, freezer, 4)
    assert len(LOG(m)) == 1 and LOG(m)[0]["estimated"] is True
