"""Config flow: pick the tank sensors, the rest is suggested automatically."""
from __future__ import annotations

from typing import Any

import voluptuous as vol

from homeassistant.config_entries import ConfigEntry, ConfigFlow, ConfigFlowResult, OptionsFlow
from homeassistant.const import CONF_NAME
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers import entity_registry as er, selector

from .const import (
    CONF_BOTTOM,
    CONF_ENERGY,
    CONF_HEATING,
    CONF_HEATING_STATES,
    CONF_LOW,
    CONF_PROFILE,
    CONF_SHOWER_TEMP,
    CONF_TOP,
    CONF_VOLUME,
    DEFAULT_HEATING_STATES,
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
PROFILE = selector.SelectSelector(selector.SelectSelectorConfig(options=PROFILES, translation_key="profile", mode=selector.SelectSelectorMode.DROPDOWN))


def suggest(hass: HomeAssistant, top: str, volume: float) -> dict[str, Any]:
    """Look at the device of the top sensor for a reheat state and a hot-water energy meter."""
    reg = er.async_get(hass)
    out: dict[str, Any] = {CONF_PROFILE: "generic"}
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
        if (st.attributes.get("device_class") == "energy" and "dhw" in eid and "defrost" not in eid
                and ("output" in eid or "out_" in eid or "produced" in eid)):
            out.setdefault(CONF_ENERGY, e.entity_id)
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
            vol.Required(CONF_SHOWER_TEMP, default=DEFAULT_SHOWER_TEMP): SHOWER,
        })
        return self.async_show_form(step_id="user", data_schema=self.add_suggested_values_to_schema(schema, user_input or {}),
                                    errors=errors)

    async def async_step_extras(self, user_input: dict[str, Any] | None = None) -> ConfigFlowResult:
        if user_input is not None:
            data = {**self._data, **user_input}
            title = data.pop(CONF_NAME)
            return self.async_create_entry(title=title, data=data)
        sug = suggest(self.hass, self._data[CONF_TOP], float(self._data[CONF_VOLUME]))
        schema = vol.Schema({
            vol.Required(CONF_PROFILE, default=sug[CONF_PROFILE]): PROFILE,
            vol.Optional(CONF_HEATING): ANY,
            vol.Optional(CONF_HEATING_STATES, default=DEFAULT_HEATING_STATES): selector.TextSelector(),
            vol.Optional(CONF_ENERGY): ENERGY,
        })
        return self.async_show_form(step_id="extras", data_schema=self.add_suggested_values_to_schema(schema, sug))

    @staticmethod
    @callback
    def async_get_options_flow(config_entry: ConfigEntry) -> OptionsFlow:
        return ShowerTimeOptionsFlow()


class ShowerTimeOptionsFlow(OptionsFlow):
    async def async_step_init(self, user_input: dict[str, Any] | None = None) -> ConfigFlowResult:
        if user_input is not None:
            return self.async_create_entry(data=user_input)
        cur = {**self.config_entry.data, **self.config_entry.options}
        cur.setdefault(CONF_LOW, DEFAULT_LOW)
        schema = vol.Schema({
            vol.Required(CONF_VOLUME): VOLUME,
            vol.Required(CONF_SHOWER_TEMP): SHOWER,
            vol.Required(CONF_LOW): LOW,
            vol.Optional(CONF_BOTTOM): TEMP,
            vol.Optional(CONF_HEATING): ANY,
            vol.Optional(CONF_HEATING_STATES, default=DEFAULT_HEATING_STATES): selector.TextSelector(),
            vol.Optional(CONF_ENERGY): ENERGY,
        })
        return self.async_show_form(step_id="init", data_schema=self.add_suggested_values_to_schema(schema, cur))
