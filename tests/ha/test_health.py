"""Learning on demand and the repair notices."""
from __future__ import annotations

from datetime import timedelta

from homeassistant.core import HomeAssistant
from homeassistant.helpers import issue_registry as ir
from pytest_homeassistant_custom_component.common import async_fire_time_changed
from pytest_homeassistant_custom_component.components.recorder.common import async_wait_recording_done

from .conftest import DOMAIN, TOP, TEMP_ATTRS, tank


async def test_learn_now_runs_every_step(hass: HomeAssistant, entry) -> None:
    """With little history the model is not updated, but the heat-loss step still runs (it used to be skipped)."""
    await async_wait_recording_done(hass)
    await hass.services.async_call("button", "press", {"entity_id": "button.hot_water_learn_now"}, blocking=True)
    await hass.async_block_till_done()
    m = entry.runtime_data
    assert m.learn_failures == 0
    assert m.loss_backfilled is True
    assert not m.learning


async def test_tank_sensor_not_reporting_raises_and_clears_a_repair(hass: HomeAssistant, entry, freezer) -> None:
    issue_id = f"{entry.entry_id}_sensor_missing_top"
    registry = ir.async_get(hass)
    hass.states.async_set(TOP, "unavailable", TEMP_ATTRS)
    await hass.async_block_till_done()
    for minutes in (10, 60, 60, 10):                         # the check runs every 10 minutes
        freezer.tick(timedelta(minutes=minutes))
        async_fire_time_changed(hass)
        await hass.async_block_till_done()
    issue = registry.async_get_issue(DOMAIN, issue_id)
    assert issue is not None and issue.translation_key == "sensor_missing"
    assert issue.translation_placeholders["entity"] == TOP

    tank(hass, 55.0, 49.8)                                   # back again
    freezer.tick(timedelta(minutes=10))
    async_fire_time_changed(hass)
    await hass.async_block_till_done()
    assert registry.async_get_issue(DOMAIN, issue_id) is None


async def test_swapped_sensors_raise_a_repair(hass: HomeAssistant, entry, freezer) -> None:
    tank(hass, 30.0, 55.0)                                   # bottom far warmer than the top for hours
    await hass.async_block_till_done()
    for _ in range(40):
        freezer.tick(timedelta(minutes=10))
        async_fire_time_changed(hass)
        await hass.async_block_till_done()
    assert ir.async_get(hass).async_get_issue(DOMAIN, f"{entry.entry_id}_sensors_swapped") is not None


async def test_removing_the_integration_removes_its_repairs(hass: HomeAssistant, entry, freezer) -> None:
    hass.states.async_set(TOP, "unavailable", TEMP_ATTRS)
    for minutes in (10, 60, 60, 10):
        freezer.tick(timedelta(minutes=minutes))
        async_fire_time_changed(hass)
        await hass.async_block_till_done()
    registry = ir.async_get(hass)
    assert any(d == DOMAIN for d, _ in registry.issues)
    assert await hass.config_entries.async_remove(entry.entry_id)
    await hass.async_block_till_done()
    assert not any(d == DOMAIN for d, _ in registry.issues)
