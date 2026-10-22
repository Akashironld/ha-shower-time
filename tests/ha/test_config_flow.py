"""The setup screens and the options screen."""
from __future__ import annotations

from homeassistant.config_entries import SOURCE_USER
from homeassistant.core import HomeAssistant
from homeassistant.data_entry_flow import FlowResultType

from .conftest import BOTTOM, DOMAIN, TEMP_ATTRS, TOP, setup_entry, tank


async def test_setup_creates_an_entry(hass: HomeAssistant, deps) -> None:
    tank(hass, 55.0, 49.8)
    result = await hass.config_entries.flow.async_init(DOMAIN, context={"source": SOURCE_USER})
    assert result["type"] is FlowResultType.FORM and result["step_id"] == "user"
    result = await hass.config_entries.flow.async_configure(result["flow_id"], {
        "name": "Hot water", "top_sensor": TOP, "bottom_sensor": BOTTOM, "tank_volume": 200,
        "temperature_mode": "auto"})
    assert result["type"] is FlowResultType.FORM and result["step_id"] == "extras"
    result = await hass.config_entries.flow.async_configure(result["flow_id"], {
        "profile": "generic", "flow_source": "unknown"})
    await hass.async_block_till_done()
    assert result["type"] is FlowResultType.CREATE_ENTRY
    assert result["title"] == "Hot water"
    assert result["data"]["top_sensor"] == TOP and "name" not in result["data"]
    assert result["data"]["bathtub"] is True                      # asked at setup: a bath on this tank (default)
    assert hass.states.get("sensor.hot_water_shower_time") is not None


async def test_same_sensor_twice_is_refused(hass: HomeAssistant, deps) -> None:
    tank(hass, 55.0, 49.8)
    result = await hass.config_entries.flow.async_init(DOMAIN, context={"source": SOURCE_USER})
    result = await hass.config_entries.flow.async_configure(result["flow_id"], {
        "name": "Hot water", "top_sensor": TOP, "bottom_sensor": TOP, "tank_volume": 200,
        "temperature_mode": "auto"})
    assert result["type"] is FlowResultType.FORM and result["errors"] == {"bottom_sensor": "same_sensor"}


async def test_a_tank_can_only_be_added_once(hass: HomeAssistant, deps) -> None:
    await setup_entry(hass)
    result = await hass.config_entries.flow.async_init(DOMAIN, context={"source": SOURCE_USER})
    result = await hass.config_entries.flow.async_configure(result["flow_id"], {
        "name": "Again", "top_sensor": TOP, "bottom_sensor": BOTTOM, "tank_volume": 200,
        "temperature_mode": "auto"})
    assert result["type"] is FlowResultType.ABORT and result["reason"] == "already_configured"


async def test_flow_needs_what_it_asks_for(hass: HomeAssistant, deps) -> None:
    tank(hass, 55.0, 49.8)
    result = await hass.config_entries.flow.async_init(DOMAIN, context={"source": SOURCE_USER})
    result = await hass.config_entries.flow.async_configure(result["flow_id"], {
        "name": "Hot water", "top_sensor": TOP, "bottom_sensor": BOTTOM, "tank_volume": 200,
        "temperature_mode": "auto"})
    result = await hass.config_entries.flow.async_configure(result["flow_id"], {
        "profile": "generic", "flow_source": "manual"})                 # manual flow, but no flow filled in
    assert result["type"] is FlowResultType.FORM and result["errors"] == {"head_flow": "head_flow_missing"}


async def test_options_suggest_and_save_a_bathroom_signal(hass: HomeAssistant, deps) -> None:
    entry = await setup_entry(hass)
    hass.states.async_set("sensor.badkamer_humidity", "58", {"unit_of_measurement": "%", "device_class": "humidity"})
    result = await hass.config_entries.options.async_init(entry.entry_id)
    assert result["type"] is FlowResultType.FORM
    suggested = {str(k): (k.description or {}).get("suggested_value") for k in result["data_schema"].schema}
    assert suggested["bathroom_sensor"] == "sensor.badkamer_humidity"              # suggested by its name
    assert suggested["bathtub"] is True                       # entries from before the question: a bath, as before
    values = {k: v for k, v in suggested.items() if v is not None}
    values.update({"tank_volume": 200, "temperature_mode": "auto", "flow_source": "unknown", "low_minutes": 10,
                   "legionella_temperature": 60, "legionella_days": 8})
    result = await hass.config_entries.options.async_configure(result["flow_id"], values)
    await hass.async_block_till_done()
    assert result["type"] is FlowResultType.CREATE_ENTRY
    assert entry.options["bathroom_sensor"] == "sensor.badkamer_humidity"
    assert entry.runtime_data.bath_signal is not None and entry.runtime_data.bath_signal.kind == "humidity"


def _suggested(result) -> dict:
    return {str(k): (k.description or {}).get("suggested_value") for k in result["data_schema"].schema}


async def test_options_can_empty_what_setup_filled_in(hass: HomeAssistant, deps) -> None:
    """#12: a COP, flow and sensor filled in at setup are kept in the entry's data; emptied in the options they
    stay empty, also when the options are opened again, and the integration stops using them."""
    entry = await setup_entry(hass, data={"cop": 3.0, "flow_source": "manual", "head_flow": 6.5,
                                          "outdoor_sensor": "sensor.outdoor"})
    m = entry.runtime_data
    assert m.cop_manual == 3.0 and m.head_flow() == (6.5, "manual") and m.outdoor == "sensor.outdoor"
    result = await hass.config_entries.options.async_init(entry.entry_id)
    suggested = _suggested(result)
    assert suggested["cop"] == 3.0 and suggested["head_flow"] == 6.5
    values = {k: v for k, v in suggested.items()
              if v is not None and k not in ("cop", "head_flow", "outdoor_sensor", "bathroom_sensor")}
    values.update({"flow_source": "unknown", "low_minutes": 10, "legionella_temperature": 60, "legionella_days": 8})
    result = await hass.config_entries.options.async_configure(result["flow_id"], values)
    await hass.async_block_till_done()
    assert result["type"] is FlowResultType.CREATE_ENTRY
    assert entry.options["cop"] is None and entry.options["head_flow"] is None and entry.options["outdoor_sensor"] is None

    m = entry.runtime_data                                     # reloaded with the new options
    assert m.cop_manual is None and m.ledger.cop_heat_pump(m)[1] != "manual"
    assert m.head_flow() == (None, "unknown") and m.outdoor is None
    result = await hass.config_entries.options.async_init(entry.entry_id)
    suggested = _suggested(result)
    assert suggested["cop"] is None and suggested["head_flow"] is None and suggested["outdoor_sensor"] is None


async def test_an_emptied_bathroom_signal_is_not_suggested_again(hass: HomeAssistant, deps) -> None:
    entry = await setup_entry(hass)
    hass.states.async_set("sensor.badkamer_humidity", "58", {"unit_of_measurement": "%", "device_class": "humidity"})
    result = await hass.config_entries.options.async_init(entry.entry_id)
    suggested = _suggested(result)
    assert suggested["bathroom_sensor"] == "sensor.badkamer_humidity"
    values = {k: v for k, v in suggested.items() if v is not None and k != "bathroom_sensor"}   # emptied
    result = await hass.config_entries.options.async_configure(result["flow_id"], values)
    await hass.async_block_till_done()
    assert entry.options["bathroom_sensor"] is None and entry.runtime_data.bath_signal is None
    result = await hass.config_entries.options.async_init(entry.entry_id)
    assert _suggested(result)["bathroom_sensor"] is None


async def test_a_bathroom_signal_emptied_at_setup_is_not_suggested_later(hass: HomeAssistant, deps) -> None:
    tank(hass, 55.0, 49.8)
    hass.states.async_set("sensor.badkamer_humidity", "58", {"unit_of_measurement": "%", "device_class": "humidity"})
    result = await hass.config_entries.flow.async_init(DOMAIN, context={"source": SOURCE_USER})
    result = await hass.config_entries.flow.async_configure(result["flow_id"], {
        "name": "Hot water", "top_sensor": TOP, "bottom_sensor": BOTTOM, "tank_volume": 200,
        "temperature_mode": "auto"})
    assert _suggested(result)["bathroom_sensor"] == "sensor.badkamer_humidity"
    result = await hass.config_entries.flow.async_configure(result["flow_id"], {
        "profile": "generic", "flow_source": "unknown"})                # the suggestion emptied
    await hass.async_block_till_done()
    entry = result["result"]
    assert entry.data["bathroom_sensor"] is None
    result = await hass.config_entries.options.async_init(entry.entry_id)
    assert _suggested(result)["bathroom_sensor"] is None
