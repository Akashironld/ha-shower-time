"""Fixtures: a Home Assistant with a recorder, the integration's dependencies and a tank to watch.

The integration serves its dashboard card through Home Assistant's web server and frontend. Those are not part of
a test run (no real web server, no frontend files), so they are stood in for: the static path registration and
the frontend's list of extra modules are plain mocks that the tests can inspect."""
from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

import pytest
from homeassistant.components.frontend import DATA_EXTRA_JS_URL_ES5, DATA_EXTRA_MODULE_URL
from homeassistant.core import HomeAssistant
from homeassistant.setup import async_setup_component
from pytest_homeassistant_custom_component.common import MockConfigEntry

DOMAIN = "shower_time"
TOP = "sensor.tank_top"
BOTTOM = "sensor.tank_bottom"
HUMIDITY = "sensor.bathroom_humidity"
TEMP_ATTRS = {"unit_of_measurement": "°C", "device_class": "temperature", "state_class": "measurement"}


def tank(hass: HomeAssistant, top: float, bottom: float) -> None:
    hass.states.async_set(TOP, str(top), TEMP_ATTRS)
    hass.states.async_set(BOTTOM, str(bottom), TEMP_ATTRS)


@pytest.fixture(autouse=True)
def auto_enable_custom_integrations(recorder_mock, enable_custom_integrations):
    """A recorder (it has to start before Home Assistant itself) and the integration from custom_components."""
    return


@pytest.fixture
async def deps(hass: HomeAssistant) -> HomeAssistant:
    """Recorder (real, in memory), websocket API, and stand-ins for the web server and frontend."""
    hass.http = MagicMock()
    hass.http.async_register_static_paths = AsyncMock()
    hass.config.components.update({"http", "frontend", "lovelace"})
    hass.data[DATA_EXTRA_MODULE_URL] = set()
    hass.data[DATA_EXTRA_JS_URL_ES5] = set()
    assert await async_setup_component(hass, "websocket_api", {})
    return hass


async def setup_entry(hass: HomeAssistant, options: dict | None = None, data: dict | None = None) -> MockConfigEntry:
    """``data``: what was filled in at setup besides the tank; ``options``: what was saved in the options since."""
    tank(hass, 55.0, 49.8)
    entry = MockConfigEntry(
        domain=DOMAIN, title="Hot water", unique_id=TOP,
        data={"top_sensor": TOP, "bottom_sensor": BOTTOM, "tank_volume": 200, "temperature_mode": "auto",
              "profile": "generic", "flow_source": "unknown", **(data or {})},
        options=options or {})
    entry.add_to_hass(hass)
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()
    return entry


@pytest.fixture
async def entry(deps: HomeAssistant) -> MockConfigEntry:
    """The integration set up on a 200 L tank with a top and a bottom sensor."""
    return await setup_entry(deps)
