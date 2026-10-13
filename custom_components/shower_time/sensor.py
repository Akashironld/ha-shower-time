"""Sensors: shower minutes, showers, litres and the learned model."""
from __future__ import annotations

from homeassistant.components.sensor import SensorDeviceClass, SensorEntity, SensorStateClass
from homeassistant.const import EntityCategory, UnitOfTime, UnitOfVolume
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from . import ShowerTimeConfigEntry
from .entity import ShowerTimeEntity


async def async_setup_entry(hass: HomeAssistant, entry: ShowerTimeConfigEntry,
                            async_add_entities: AddEntitiesCallback) -> None:
    m = entry.runtime_data
    async_add_entities([MinutesSensor(m), ShowersSensor(m), LitresSensor(m), ModelSensor(m)])


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
            "hot_fraction": round(e.hot_fraction, 2),
            "tank_average": round(e.average, 1),
            "top_temperature": m.top_value,
            "bottom_temperature": m.bottom_value,
            "cold_water": round(e.cold, 1),
            "flow": round(p["flow"]["v"], 1),
            "shower_temperature": m.shower_temp,
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
            "typical_shower_minutes": p["duration"]["v"],
            "empty_factor_phi": p["phi"]["v"],
            "standing_loss_k_per_h": p["loss"]["v"],
            "stratification": {k: v["v"] for k, v in p["w"].items()},
            "cold_water_by_month": p["cold"],
            "usual_full_temperature": p.get("full_temp"),
            "observations": {k: p[k]["n"] for k in ("flow", "phi", "loss", "duration")},
            "last_learn": m.last_learn,
            "last_learn_samples": m.last_samples,
        }
