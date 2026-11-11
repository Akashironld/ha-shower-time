"""Card address, legionella coverage, currency, reconfigure reset, Fahrenheit, stored data of older versions."""
from __future__ import annotations

import asyncio
import json
from datetime import timedelta
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from homeassistant.components.frontend import DATA_EXTRA_JS_URL_ES5, DATA_EXTRA_MODULE_URL
from homeassistant.core import HomeAssistant
from homeassistant.util import dt as dt_util
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.shower_time import CARD_URL, _async_register_resource, async_card_url, model
from custom_components.shower_time.manager import _temp

from .conftest import BOTTOM, DOMAIN, TEMP_ATTRS, TOP, setup_entry
from .test_live import SHOWERING, start_reading, state, step
from .test_setup import _resources

COMP = Path(__file__).parents[2] / "custom_components" / "shower_time"


# ------------------------------------------------------------------ card address
async def test_card_url_follows_the_file(hass: HomeAssistant, tmp_path) -> None:
    card = tmp_path / "card.js"
    card.write_text("one")
    a = await async_card_url(hass, card, "0.14.0")
    card.write_text("two")
    b = await async_card_url(hass, card, "0.14.0")
    prefix = CARD_URL + "?v=0.14.0-"
    assert a.startswith(prefix) and b.startswith(prefix) and a != b and len(a) == len(prefix) + 8
    card.write_text("one")
    assert await async_card_url(hass, card, "0.14.0") == a


async def test_setup_uses_one_url_everywhere(hass: HomeAssistant, entry) -> None:
    modern, legacy = hass.data[DATA_EXTRA_MODULE_URL], hass.data[DATA_EXTRA_JS_URL_ES5]
    url = next(iter(modern))
    assert modern == legacy and len(url.split("?v=")[1].split("-")[-1]) == 8


@pytest.mark.parametrize("old,changed", [(CARD_URL + "?v=0.14.0", True), (CARD_URL + "?v=0.14.0-hud4", True),
                                         (CARD_URL, True), (CARD_URL + "?v=0.14.1-abcd1234", False)])
async def test_resource_is_updated_never_duplicated(hass: HomeAssistant, deps, old, changed) -> None:
    res = _resources(hass)
    hass.data["lovelace"] = SimpleNamespace(resources=res)
    new = CARD_URL + "?v=0.14.1-abcd1234"
    await res.async_get_info()
    await res.async_create_item({"res_type": "module", "url": old})
    await res.async_create_item({"res_type": "module", "url": "/other/card.js?v=1"})
    res.async_update_item = AsyncMock(wraps=res.async_update_item)
    assert await _async_register_resource(hass, new)
    assert res.async_update_item.called is changed             # a current address is left alone
    ours = [i for i in res.async_items() if i["url"].split("?")[0] == CARD_URL]
    assert [i["url"] for i in ours] == [new] and len(res.async_items()) == 2
    assert any(i["url"] == "/other/card.js?v=1" for i in res.async_items())


async def test_setup_works_without_lovelace_resources(hass: HomeAssistant, deps) -> None:
    """Lovelace is only an after-dependency: when it is not there the card goes in through the start page."""
    manifest = json.loads((COMP / "manifest.json").read_text())
    assert "lovelace" not in manifest["dependencies"] and "lovelace" in manifest["after_dependencies"]
    with patch("custom_components.shower_time._resources", return_value=None):
        entry = await setup_entry(hass)
    assert entry.runtime_data is not None
    assert len(hass.data[DATA_EXTRA_MODULE_URL]) == 1 and hass.data[DATA_EXTRA_MODULE_URL] == hass.data[DATA_EXTRA_JS_URL_ES5]


# ------------------------------------------------------------------ legionella
async def test_a_tank_with_a_few_days_of_statistics_is_not_overdue(hass: HomeAssistant, deps) -> None:
    entry = await setup_entry(hass, data={"legionella_days": 7})
    m = entry.runtime_data
    await hass.async_block_till_done(wait_background_tasks=True)
    now = dt_util.utcnow()
    m.legionella_checked, m.legionella_at = True, None
    m.legionella_since = (now - timedelta(days=3)).isoformat()
    assert m.legionella_overdue() is None and {"key": "legionella", "days": None} not in m.problems()
    m.legionella_since = (now - timedelta(days=8)).isoformat()
    assert m.legionella_overdue() is True


async def test_the_statistics_set_how_far_back_no_hit_counts(hass: HomeAssistant, deps) -> None:
    entry = await setup_entry(hass, data={"legionella_days": 7})
    m = entry.runtime_data
    await hass.async_block_till_done(wait_background_tasks=True)
    m.legionella_checked, m.legionella_at, m.legionella_since = False, None, dt_util.utcnow().isoformat()
    first = (dt_util.utcnow() - timedelta(days=3)).timestamp()
    rows = [{"start": first + h * 3600, "max": 50.0} for h in range(60)]       # three days, never hot enough
    with patch("custom_components.shower_time.manager.statistics.statistics_during_period",
               return_value={TOP: rows, BOTTOM: rows}):
        await m._init_legionella()
    assert m.legionella_checked and m.legionella_at is None
    assert abs(dt_util.parse_datetime(m.legionella_since).timestamp() - first) < 1
    assert m.legionella_overdue() is None                                     # 3 days of 7: not proven
    hot = rows[:10] + [{"start": first + 20 * 3600, "max": 62.0}]
    m.legionella_at = None
    with patch("custom_components.shower_time.manager.statistics.statistics_during_period",
               return_value={TOP: hot, BOTTOM: hot}):
        await m._init_legionella()
    assert m.legionella_at is not None and m.legionella_overdue() is False


# ------------------------------------------------------------------ history requests
async def test_concurrent_history_requests_share_one_computation(hass: HomeAssistant, entry) -> None:
    m = entry.runtime_data
    calls = 0
    real = m._compute_history

    async def counted(hours, now):
        nonlocal calls
        calls += 1
        await real(hours, now)
    m._compute_history = counted
    a, b, c = await asyncio.gather(m.async_history(6), m.async_history(6, True), m.async_events(6))
    assert calls == 1 and a == [p[:2] for p in b] and c["points"] == a


# ------------------------------------------------------------------ currency
async def test_the_currency_unit_stays_once_known(hass: HomeAssistant, deps) -> None:
    hass.states.async_set("sensor.price", "0.25", {"unit_of_measurement": "EUR/kWh"})
    entry = await setup_entry(hass, data={"price_sensor": "sensor.price"})
    led = entry.runtime_data.ledger
    assert led.currency == hass.config.currency          # a new installation: Home Assistant's own currency
    led.note_currency("$")
    assert led.currency == hass.config.currency          # a later, different one is ignored
    fresh = type(led)()
    fresh.note_currency("€")
    fresh.note_currency("$")
    assert fresh.currency == "€"
    old = dict(led.to_dict(), currency="")               # an existing installation that never learned it
    fresh.load(old)
    assert fresh.currency == ""


# ------------------------------------------------------------------ reconfigure: top sensor changes
async def test_a_new_top_sensor_restarts_the_height_learning_only(hass: HomeAssistant, deps) -> None:
    entry = await setup_entry(hass, data={"tank_type": "weheat_wbl200"})
    m = entry.runtime_data
    await hass.async_block_till_done(wait_background_tasks=True)
    start_top = m.params["geo"]["start"][0]
    m.params["geo"]["top"] = start_top + 0.07           # learned
    m.params["geo"]["n"] = 12
    m.last_learn = "2026-10-15T10:00:00+00:00"
    hass.states.async_set("sensor.new_top", "54", TEMP_ATTRS)

    async def reconfigure(top: str, bottom: str):
        result = await entry.start_reconfigure_flow(hass)
        await hass.config_entries.flow.async_configure(result["flow_id"], {"top_sensor": top, "bottom_sensor": bottom})
        await hass.async_block_till_done(wait_background_tasks=True)
        return entry.runtime_data

    hass.states.async_set("sensor.new_bottom", "40", TEMP_ATTRS)
    m2 = await reconfigure(TOP, "sensor.new_bottom")             # only the bottom sensor: nothing is reset
    assert m2.params["geo"]["top"] == pytest.approx(start_top + 0.07) and m2.params["geo"]["n"] == 12
    m3 = await reconfigure("sensor.new_top", "sensor.new_bottom")
    assert m3.params["geo"]["top"] == start_top and m3.params["geo"]["n"] == 0
    assert m3.last_learn == "2026-10-15T10:00:00+00:00"          # the rest is kept


# ------------------------------------------------------------------ Fahrenheit
def test_temp_in_fahrenheit() -> None:
    st = lambda v: SimpleNamespace(state=str(v), attributes={})
    assert _temp(st(131), True) == pytest.approx(55.0)
    assert _temp(st(131), False) is None                         # 131 °C is no tank
    assert _temp(st(32), True) is None and _temp(st(-1), True) is None and _temp(st("unavailable"), True) is None


async def test_a_tank_in_fahrenheit_end_to_end(hass: HomeAssistant, deps, freezer) -> None:
    f = {"unit_of_measurement": "°F", "device_class": "temperature", "state_class": "measurement"}
    set_f = lambda top, bottom: (hass.states.async_set(TOP, str(round(top * 9 / 5 + 32, 1)), f),
                                 hass.states.async_set(BOTTOM, str(round(bottom * 9 / 5 + 32, 1)), f))
    set_f(55.0, 49.8)
    entry = MockConfigEntry(domain=DOMAIN, title="Hot water", unique_id=TOP,
                            data={"top_sensor": TOP, "bottom_sensor": BOTTOM, "tank_volume": 200, "temperature_mode": "auto",
                                  "profile": "generic", "flow_source": "unknown"})
    entry.add_to_hass(hass)
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()
    m = entry.runtime_data
    assert m._is_f(TOP) and m.top_value == pytest.approx(55.0, abs=0.05) and m.bottom_value == pytest.approx(49.8, abs=0.05)
    celsius = model.estimate(m.params, 55.0, 49.8, volume=200, shower_temp=m.shower_temp, month=dt_util.now().month,
                             head_flow=None, doy=dt_util.now().timetuple().tm_yday, ema=m.ema_now, hot=m.hot_top)
    assert float(state(hass, "sensor.hot_water_shower_time")) == pytest.approx(celsius.minutes, abs=1.0)
    await step_f(hass, freezer, set_f, 1, 55.0, 50.0)
    for bottom in (47.0, 44.0, 41.0, 38.0, 35.0):
        await step_f(hass, freezer, set_f, 2, 55.0, bottom)
    assert state(hass, SHOWERING) == "on"                        # a fall in °F is followed like one in °C


async def step_f(hass, freezer, set_f, minutes, top, bottom) -> None:
    from pytest_homeassistant_custom_component.common import async_fire_time_changed
    freezer.tick(timedelta(minutes=minutes))
    set_f(top, bottom)
    async_fire_time_changed(hass)
    await hass.async_block_till_done()


# ------------------------------------------------------------------ stored data of an older version
async def test_loads_data_stored_by_an_older_version(hass: HomeAssistant, deps, hass_storage) -> None:
    params = model.new_params("generic")
    for key in ("head_flow", "cold_model", "cold_samples", "reheat_rate", "cold_version"):
        params.pop(key, None)
    entry_id = "old_entry"
    now = dt_util.now()
    hass_storage[f"shower_time.{entry_id}"] = {"version": 1, "key": f"shower_time.{entry_id}", "data": {
        "profile": "generic", "params": params,
        # entries from before the kind, the source and the totals existed
        "ledger": {"log": [{"start": now.isoformat(timespec="minutes"), "minutes": 8.0, "heat_kwh": 1.4,
                            "electricity_kwh": 0.4, "cost": 0.1}]}}}
    from .conftest import tank
    tank(hass, 55.0, 49.8)
    entry = MockConfigEntry(domain=DOMAIN, title="Hot water", unique_id=TOP, entry_id=entry_id,
                            data={"top_sensor": TOP, "bottom_sensor": BOTTOM, "tank_volume": 200,
                                  "temperature_mode": "auto", "profile": "generic", "flow_source": "unknown"})
    entry.add_to_hass(hass)
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done(wait_background_tasks=True)
    m = entry.runtime_data
    assert m.params["head_flow"]["n"] == 0 and "cold_model" in m.params
    assert len(m.ledger.showers()) == 1 and m.ledger.summary()["today_showers"] == 1
    assert not m.showering and m._resume is None and m.use is None          # nothing of the restart keys
    assert m.legionella_since is not None and m.ledger.totals["showers"] == 0
    assert float(state(hass, "sensor.hot_water_shower_time")) > 0
    await hass.config_entries.async_unload(entry.entry_id)
    assert hass_storage[f"shower_time.{entry_id}"]["data"]["top_sensor"] == TOP
