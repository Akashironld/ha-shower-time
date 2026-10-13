"""Sensors: shower minutes, showers, litres and the learned model."""
from __future__ import annotations

from homeassistant.components.sensor import SensorDeviceClass, SensorEntity, SensorStateClass
from homeassistant.const import PERCENTAGE, EntityCategory, UnitOfTemperature, UnitOfTime, UnitOfVolume
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from . import ShowerTimeConfigEntry
from .entity import ShowerTimeEntity


async def async_setup_entry(hass: HomeAssistant, entry: ShowerTimeConfigEntry,
                            async_add_entities: AddEntitiesCallback) -> None:
    m = entry.runtime_data
    async_add_entities([MinutesSensor(m), ShowersSensor(m), LitresSensor(m), ShowerTemperatureSensor(m), FillSensor(m), ModelSensor(m)])


class MinutesSensor(ShowerTimeEntity, SensorEntity):
    """Main sensor; the card reads everything it needs from its attributes."""

    _attr_device_class = SensorDeviceClass.DURATION
    _attr_native_unit_of_measurement = UnitOfTime.MINUTES
    _attr_state_class = SensorStateClass.MEASUREMENT
    _attr_suggested_display_precision = 0
    _attr_icon = "mdi:shower-head"

    def __init__(self, manager) -> None:
        super().__init__(manager, "shower_minutes")

    @property
    def native_value(self):
        e = self.manager.estimate
        return round(e.minutes) if e else None

    @property
    def extra_state_attributes(self):
        m, e = self.manager, self.manager.estimate
        if e is None:
            return None
        p = m.params
        return {
            "showers": e.showers,
            "typical_shower_minutes": round(p["duration"]["v"]),
            "litres": round(e.liters, -1),
            "full_tank_minutes": round(e.full_minutes) if e.full_minutes is not None else None,
            "fill_percent": m.fill_percent(),
            "full_reference_minutes": round(m.full_ref) if m.full_ref is not None else None,
            "full_reference_at": m.full_ref_at,
            "hot_fraction": round(e.hot_fraction, 2),
            "tank_average": round(e.average, 1),
            "top_temperature": m.top_value,
            "bottom_temperature": m.bottom_value,
            "cold_water": round(e.cold, 1),
            "cold_water_source": m.cold_source(),
            "outdoor_average": m.ema_now,
            "flow": round(e.flow, 1),
            "shower_temperature": round(e.shower_temp, 1),
            "shower_temperature_mode": ("fixed" if m.temp_mode == "fixed" else ("sensed" if e.shower_temp_sensed else "reference")),
            "head_flow_source": m.head_flow()[1],
            "reheating": m.reheating,
            "low_threshold": m.low_minutes,
            "learned_at": m.last_learn,
            "observations": p["flow"]["n"],
        }


class ShowersSensor(ShowerTimeEntity, SensorEntity):
    _attr_state_class = SensorStateClass.MEASUREMENT
    _attr_icon = "mdi:shower"

    def __init__(self, manager) -> None:
        super().__init__(manager, "showers")

    @property
    def native_value(self):
        e = self.manager.estimate
        return e.showers if e else None


class LitresSensor(ShowerTimeEntity, SensorEntity):
    _attr_device_class = SensorDeviceClass.VOLUME
    _attr_native_unit_of_measurement = UnitOfVolume.LITERS
    _attr_state_class = SensorStateClass.MEASUREMENT
    _attr_suggested_display_precision = 0
    _attr_icon = "mdi:water-thermometer"

    def __init__(self, manager) -> None:
        super().__init__(manager, "hot_water")

    @property
    def native_value(self):
        e = self.manager.estimate
        return round(e.liters) if e else None


class FillSensor(ShowerTimeEntity, SensorEntity):
    """How full the tank is: 100 % right after the last reheat, dropping with use and standing loss."""

    _attr_native_unit_of_measurement = PERCENTAGE
    _attr_state_class = SensorStateClass.MEASUREMENT
    _attr_icon = "mdi:water-boiler"

    def __init__(self, manager) -> None:
        super().__init__(manager, "fill_level")

    @property
    def native_value(self):
        return self.manager.fill_percent()

    @property
    def extra_state_attributes(self):
        m = self.manager
        return {"full_reference_minutes": round(m.full_ref) if m.full_ref is not None else None,
                "full_reference_at": m.full_ref_at}


class ShowerTemperatureSensor(ShowerTimeEntity, SensorEntity):
    """The temperature you shower at: set by you, or sensed from the learned heat and a known shower-head flow."""

    _attr_device_class = SensorDeviceClass.TEMPERATURE
    _attr_native_unit_of_measurement = UnitOfTemperature.CELSIUS
    _attr_state_class = SensorStateClass.MEASUREMENT
    _attr_suggested_display_precision = 1
    _attr_icon = "mdi:thermometer-water"

    def __init__(self, manager) -> None:
        super().__init__(manager, "shower_temperature")

    @property
    def available(self) -> bool:
        e = self.manager.estimate
        return e is not None and (self.manager.temp_mode == "fixed" or e.shower_temp_sensed)

    @property
    def native_value(self):
        e = self.manager.estimate
        return round(e.shower_temp, 1) if e else None

    @property
    def extra_state_attributes(self):
        flow, source = self.manager.head_flow()
        return {"mode": "fixed" if self.manager.temp_mode == "fixed" else "sensed", "head_flow": flow, "head_flow_source": source}


class ModelSensor(ShowerTimeEntity, SensorEntity):
    """What has been learned so far (state: shower flow in L/min)."""

    _attr_entity_category = EntityCategory.DIAGNOSTIC
    _attr_native_unit_of_measurement = "L/min"
    _attr_icon = "mdi:brain"

    def __init__(self, manager) -> None:
        super().__init__(manager, "model")

    @property
    def available(self) -> bool:
        return True

    @property
    def native_value(self):
        return round(self.manager.params["flow"]["v"], 2)

    @property
    def extra_state_attributes(self):
        m = self.manager
        p = m.params
        return {
            "profile": m.profile,
            "flow_l_min": p["flow"]["v"],
            "temperature_mode": m.temp_mode,
            "reference_temperature": m.shower_temp,
            "head_flow_learned": p["head_flow"]["v"] if p["head_flow"]["n"] else None,
            "head_flow_observations": p["head_flow"]["n"],
            "typical_shower_minutes": p["duration"]["v"],
            "empty_factor_phi": p["phi"]["v"],
            "standing_loss_k_per_h": p["loss"]["v"],
            "stratification": {k: v["v"] for k, v in p["w"].items()},
            "cold_water_by_month": p["cold"],
            "cold_water_model": {"a": p["cold_model"]["a"]["v"], "b": p["cold_model"]["b"]["v"],
                                 "samples": len(p.get("cold_samples", []))},
            "outdoor_average_3w": m.ema_now,
            "usual_full_temperature": p.get("full_temp"),
            "observations": {k: p[k]["n"] for k in ("flow", "phi", "loss", "duration")},
            "last_learn": m.last_learn,
            "last_learn_samples": m.last_samples,
        }
