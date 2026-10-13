"""Config flow: pick the tank sensors, the rest is suggested automatically."""
from __future__ import annotations

from typing import Any

import voluptuous as vol

from homeassistant.config_entries import ConfigEntry, ConfigFlow, ConfigFlowResult, OptionsFlow
from homeassistant.const import CONF_NAME
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers import entity_registry as er, selector

from .const import (
    CONF_BATH,
    CONF_BOTTOM,
    CONF_ENERGY,
    CONF_FLOW_SOURCE,
    CONF_COP,
    CONF_ELEC,
    CONF_HEAD_FLOW,
    CONF_OUTDOOR,
    CONF_PRICE,
    DEFAULT_COP,
    CONF_TEMP_MODE,
    CONF_WATER,
    FLOW_SOURCES,
    TEMP_MODES,
    CONF_HEATING,
    CONF_HEATING_STATES,
    CONF_LEGIONELLA_DAYS,
    CONF_LEGIONELLA_TEMP,
    CONF_LOW,
    CONF_PROFILE,
    CONF_SHOWER_TEMP,
    CONF_TOP,
    CONF_VOLUME,
    DEFAULT_HEATING_STATES,
    DEFAULT_LEGIONELLA_DAYS,
    DEFAULT_LEGIONELLA_TEMP,
    DEFAULT_LOW,
    DEFAULT_NAME,
    DEFAULT_SHOWER_TEMP,
    DEFAULT_VOLUME,
    DOMAIN,
    PROFILES,
)

TEMP = selector.EntitySelector(selector.EntitySelectorConfig(domain="sensor", device_class="temperature"))
ENERGY = selector.EntitySelector(selector.EntitySelectorConfig(domain="sensor", device_class="energy"))
ANY = selector.EntitySelector(selector.EntitySelectorConfig(domain=["sensor", "binary_sensor", "select", "water_heater", "climate", "switch"]))
VOLUME = selector.NumberSelector(selector.NumberSelectorConfig(min=30, max=1000, step=10, unit_of_measurement="L", mode=selector.NumberSelectorMode.BOX))
SHOWER = selector.NumberSelector(selector.NumberSelectorConfig(min=30, max=45, step=0.5, unit_of_measurement="°C", mode=selector.NumberSelectorMode.BOX))
LOW = selector.NumberSelector(selector.NumberSelectorConfig(min=0, max=60, step=1, unit_of_measurement="min", mode=selector.NumberSelectorMode.BOX))
TEMP_MODE = selector.SelectSelector(selector.SelectSelectorConfig(options=TEMP_MODES, translation_key="temperature_mode", mode=selector.SelectSelectorMode.LIST))
FLOW_SOURCE = selector.SelectSelector(selector.SelectSelectorConfig(options=FLOW_SOURCES, translation_key="flow_source", mode=selector.SelectSelectorMode.DROPDOWN))
HEAD_FLOW = selector.NumberSelector(selector.NumberSelectorConfig(min=2, max=25, step=0.1, unit_of_measurement="L/min", mode=selector.NumberSelectorMode.BOX))
WATER = selector.EntitySelector(selector.EntitySelectorConfig(domain="sensor", device_class=["water", "volume_flow_rate", "volume"]))
BATH = selector.EntitySelector(selector.EntitySelectorConfig(
    domain=["sensor", "binary_sensor", "switch", "fan", "light", "input_boolean"]))
PRICE = selector.EntitySelector(selector.EntitySelectorConfig(domain=["sensor", "input_number"]))
COP = selector.NumberSelector(selector.NumberSelectorConfig(min=0.8, max=6, step=0.1, mode=selector.NumberSelectorMode.BOX))
LEGIONELLA_TEMP = selector.NumberSelector(selector.NumberSelectorConfig(min=50, max=75, step=1, unit_of_measurement="°C", mode=selector.NumberSelectorMode.BOX))
LEGIONELLA_DAYS = selector.NumberSelector(selector.NumberSelectorConfig(min=1, max=60, step=1, unit_of_measurement="d", mode=selector.NumberSelectorMode.BOX))
PROFILE = selector.SelectSelector(selector.SelectSelectorConfig(options=PROFILES, translation_key="profile", mode=selector.SelectSelectorMode.DROPDOWN))


def suggest_bathroom(hass: HomeAssistant) -> str | None:
    """A bathroom humidity sensor, else the power of a bathroom fan, else a bathroom fan itself."""
    reg = er.async_get(hass)
    words = ("badkamer", "bathroom", "badezimmer", "salle_de_bain", "douche", "shower", "bad_")
    best: tuple[int, str] | None = None
    for st in hass.states.async_all(("sensor", "fan", "switch")):
        eid = st.entity_id.lower()
        ent = reg.async_get(st.entity_id)
        if (ent and ent.platform == DOMAIN) or not any(w in eid for w in words):
            continue
        dc = st.attributes.get("device_class")
        if st.domain == "sensor" and dc == "humidity":
            rank = 0
        elif st.domain == "sensor" and dc == "power" and any(k in eid for k in ("ventil", "fan", "luefter", "afzuig")):
            rank = 1
        elif st.domain in ("fan", "switch") and any(k in eid for k in ("ventil", "fan", "luefter", "afzuig")):
            rank = 2
        else:
            continue
        if best is None or rank < best[0]:
            best = (rank, st.entity_id)
    return best[1] if best else None


def suggest(hass: HomeAssistant, top: str, volume: float) -> dict[str, Any]:
    """Look at the device of the top sensor for a reheat state and a hot-water energy meter."""
    reg = er.async_get(hass)
    out: dict[str, Any] = {CONF_PROFILE: "generic"}
    if bath := suggest_bathroom(hass):
        out[CONF_BATH] = bath
    ent = reg.async_get(top)
    if ent is None:
        return out
    if ent.platform == "weheat" and abs(volume - 200) < 1:
        out[CONF_PROFILE] = "weheat_200"
    if not ent.device_id:
        return out
    siblings = er.async_entries_for_device(reg, ent.device_id)
    keywords = {"dhw", "hot_water", "legionella", "heating_dhw"}
    for e in siblings:
        st = hass.states.get(e.entity_id)
        if st is None:
            continue
        opts = [str(o).lower() for o in (st.attributes.get("options") or [])]
        if CONF_HEATING not in out and any(any(k in o for k in keywords) for o in opts):
            out[CONF_HEATING] = e.entity_id
        eid = e.entity_id.lower()
        if (st.attributes.get("device_class") == "temperature" and CONF_OUTDOOR not in out
                and any(k in eid for k in ("outside", "outdoor", "buiten", "air_in", "ambient"))):
            out[CONF_OUTDOOR] = e.entity_id
        if (st.attributes.get("device_class") == "energy" and "dhw" in eid and "defrost" not in eid
                and ("output" in eid or "out_" in eid or "produced" in eid)):
            out.setdefault(CONF_ENERGY, e.entity_id)
        if (st.attributes.get("device_class") == "energy" and "dhw" in eid and "defrost" not in eid
                and any(k in eid for k in ("electricity", "used", "consum", "input", "power_in"))):
            out.setdefault(CONF_ELEC, e.entity_id)
    return out


class ShowerTimeConfigFlow(ConfigFlow, domain=DOMAIN):
    VERSION = 1

    def __init__(self) -> None:
        self._data: dict[str, Any] = {}

    async def async_step_user(self, user_input: dict[str, Any] | None = None) -> ConfigFlowResult:
        errors: dict[str, str] = {}
        if user_input is not None:
            if user_input.get(CONF_BOTTOM) == user_input[CONF_TOP]:
                errors[CONF_BOTTOM] = "same_sensor"
            else:
                await self.async_set_unique_id(user_input[CONF_TOP])
                self._abort_if_unique_id_configured()
                self._data = user_input
                return await self.async_step_extras()
        schema = vol.Schema({
            vol.Required(CONF_NAME, default=DEFAULT_NAME): selector.TextSelector(),
            vol.Required(CONF_TOP): TEMP,
            vol.Optional(CONF_BOTTOM): TEMP,
            vol.Required(CONF_VOLUME, default=DEFAULT_VOLUME): VOLUME,
            vol.Required(CONF_TEMP_MODE, default="auto"): TEMP_MODE,
            vol.Optional(CONF_SHOWER_TEMP, default=DEFAULT_SHOWER_TEMP): SHOWER,
        })
        return self.async_show_form(step_id="user", data_schema=self.add_suggested_values_to_schema(schema, user_input or {}),
                                    errors=errors)

    async def async_step_extras(self, user_input: dict[str, Any] | None = None) -> ConfigFlowResult:
        errors: dict[str, str] = {}
        if user_input is not None:
            errors = _check_flow(user_input)
            if not errors:
                data = {**self._data, **user_input}
                title = data.pop(CONF_NAME)
                return self.async_create_entry(title=title, data=data)
        sug = suggest(self.hass, self._data[CONF_TOP], float(self._data[CONF_VOLUME]))
        schema = vol.Schema({
            vol.Required(CONF_PROFILE, default=sug[CONF_PROFILE]): PROFILE,
            vol.Optional(CONF_HEATING): ANY,
            vol.Optional(CONF_HEATING_STATES, default=DEFAULT_HEATING_STATES): selector.TextSelector(),
            vol.Optional(CONF_ENERGY): ENERGY,
            vol.Optional(CONF_OUTDOOR): TEMP,
            vol.Optional(CONF_ELEC): ENERGY,
            vol.Optional(CONF_COP, default=DEFAULT_COP): COP,
            vol.Optional(CONF_PRICE): PRICE,
            vol.Required(CONF_FLOW_SOURCE, default="unknown"): FLOW_SOURCE,
            vol.Optional(CONF_HEAD_FLOW): HEAD_FLOW,
            vol.Optional(CONF_WATER): WATER,
            vol.Optional(CONF_BATH): BATH,
        })
        return self.async_show_form(step_id="extras", data_schema=self.add_suggested_values_to_schema(schema, user_input or sug),
                                    errors=errors)

    @staticmethod
    @callback
    def async_get_options_flow(config_entry: ConfigEntry) -> OptionsFlow:
        return ShowerTimeOptionsFlow()


class ShowerTimeOptionsFlow(OptionsFlow):
    async def async_step_init(self, user_input: dict[str, Any] | None = None) -> ConfigFlowResult:
        errors: dict[str, str] = {}
        if user_input is not None:
            errors = _check_flow(user_input)
            if not errors:
                return self.async_create_entry(data=user_input)
        cur = {**self.config_entry.data, **self.config_entry.options}
        cur.setdefault(CONF_TEMP_MODE, "fixed" if CONF_SHOWER_TEMP in cur else "auto")
        cur.setdefault(CONF_FLOW_SOURCE, "unknown")
        cur.setdefault(CONF_LOW, DEFAULT_LOW)
        cur.setdefault(CONF_LEGIONELLA_TEMP, DEFAULT_LEGIONELLA_TEMP)
        cur.setdefault(CONF_LEGIONELLA_DAYS, DEFAULT_LEGIONELLA_DAYS)
        if CONF_BATH not in cur and (bath := suggest_bathroom(self.hass)):
            cur[CONF_BATH] = bath                   # suggested only: saved when you press submit
        schema = vol.Schema({
            vol.Required(CONF_VOLUME): VOLUME,
            vol.Required(CONF_TEMP_MODE): TEMP_MODE,
            vol.Optional(CONF_SHOWER_TEMP, default=DEFAULT_SHOWER_TEMP): SHOWER,
            vol.Required(CONF_FLOW_SOURCE): FLOW_SOURCE,
            vol.Optional(CONF_HEAD_FLOW): HEAD_FLOW,
            vol.Optional(CONF_WATER): WATER,
            vol.Required(CONF_LOW): LOW,
            vol.Optional(CONF_BOTTOM): TEMP,
            vol.Optional(CONF_HEATING): ANY,
            vol.Optional(CONF_HEATING_STATES, default=DEFAULT_HEATING_STATES): selector.TextSelector(),
            vol.Optional(CONF_ENERGY): ENERGY,
            vol.Optional(CONF_OUTDOOR): TEMP,
            vol.Optional(CONF_ELEC): ENERGY,
            vol.Optional(CONF_COP, default=DEFAULT_COP): COP,
            vol.Optional(CONF_PRICE): PRICE,
            vol.Optional(CONF_BATH): BATH,
            vol.Required(CONF_LEGIONELLA_TEMP): LEGIONELLA_TEMP,
            vol.Required(CONF_LEGIONELLA_DAYS): LEGIONELLA_DAYS,
        })
        return self.async_show_form(step_id="init", data_schema=self.add_suggested_values_to_schema(schema, user_input or cur),
                                    errors=errors)


def _check_flow(user_input: dict[str, Any]) -> dict[str, str]:
    source = user_input.get(CONF_FLOW_SOURCE, "unknown")
    if source == "manual" and not user_input.get(CONF_HEAD_FLOW):
        return {CONF_HEAD_FLOW: "head_flow_missing"}
    if source == "water_meter" and not user_input.get(CONF_WATER):
        return {CONF_WATER: "water_missing"}
    return {}
