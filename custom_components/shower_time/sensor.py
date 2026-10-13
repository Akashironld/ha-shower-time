"""Sensors: shower minutes, showers, litres and the learned model."""
from __future__ import annotations

from homeassistant.components.sensor import SensorDeviceClass, SensorEntity, SensorStateClass
from homeassistant.const import (
    PERCENTAGE,
    EntityCategory,
    UnitOfEnergy,
    UnitOfPower,
    UnitOfTemperature,
    UnitOfTime,
    UnitOfVolume,
)
from homeassistant.util import dt as dt_util
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from . import ShowerTimeConfigEntry
from .entity import ShowerTimeEntity


async def async_setup_entry(hass: HomeAssistant, entry: ShowerTimeConfigEntry,
                            async_add_entities: AddEntitiesCallback) -> None:
    m = entry.runtime_data
    async_add_entities([MinutesSensor(m), ShowersSensor(m), LitresSensor(m), ShowerTemperatureSensor(m), FillSensor(m), ModelSensor(m),
                        ShowersTodaySensor(m), ShowerTimeTodaySensor(m), ShowerTimeWeekSensor(m), LastShowerSensor(m),
                        ReheatReadySensor(m), CurrentShowerSensor(m), ExpectedTodaySensor(m), ShowersTotalSensor(m),
                        ShowerTimeTotalSensor(m), ShowerEnergyTotalSensor(m), LegionellaSensor(m), HeatLossSensor(m),
                        OtherHotWaterTodaySensor(m)]
                       + ([ShowerCostTodaySensor(m), ShowerCostTotalSensor(m)] if m.price else []))


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
        e = self.manager.current
        return round(e.minutes) if e else None

    @property
    def extra_state_attributes(self):
        m, e = self.manager, self.manager.current
        if e is None:
            return None
        p = m.params
        return {
            "showers": e.showers,
            "typical_shower_minutes": round(p["duration"]["v"]),
            "litres": round(e.liters, -1),
            "full_tank_minutes": round(e.full_minutes) if e.full_minutes is not None else None,
            "fill_percent": m.fill_percent(),
            "full_reference_minutes": round(m.ref_minutes()) if m.ref_minutes() is not None else None,
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
            "showering": m.showering,
            **{k: v for k, v in m.ledger.summary().items()},
            "currency": m.ledger.currency or None,
            "hot_water_price_per_kwh": round(m.ledger.tank_price, 3) if m.ledger.tank_price is not None else None,
            "cop": m.ledger.cop(m),
            "since_reheat": m.since_reheat(),
            "standing_loss_per_day": m.standing_loss_per_day(),
            "reheat_full_at": m.ledger.eta_full.isoformat(timespec="minutes") if m.ledger.eta_full else None,
            "reheat_one_shower_at": m.ledger.eta_one.isoformat(timespec="minutes") if m.ledger.eta_one else None,
            "live_countdown": m.live_offset > 0,
            "low_threshold": m.low_minutes,
            "current_shower": m.ledger.running(m, dt_util.utcnow()),
            "hot_water_use": {"kind": m.use_kind(), "bathroom_signal": m.bath_active()} if m.showering else None,
            "other_today": m.ledger.other_today(),
            "forecast": m.forecast_attrs(),
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
        e = self.manager.current
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
        e = self.manager.current
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
        return {"full_reference_minutes": round(m.ref_minutes()) if m.ref_minutes() is not None else None,
                "full_reference_tank_average": round(m.full_ref_avg, 1) if m.full_ref_avg is not None else None,
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
        e = self.manager.current
        return e is not None and (self.manager.temp_mode == "fixed" or e.shower_temp_sensed)

    @property
    def native_value(self):
        e = self.manager.current
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



class _LogSensor(ShowerTimeEntity, SensorEntity):
    """Counters from the shower log; always available (zero when nothing happened yet)."""

    @property
    def available(self) -> bool:
        return True


class ShowersTodaySensor(_LogSensor):
    _attr_state_class = SensorStateClass.MEASUREMENT
    _attr_icon = "mdi:counter"

    def __init__(self, manager) -> None:
        super().__init__(manager, "showers_today")

    @property
    def native_value(self):
        return self.manager.ledger.summary()["today_showers"]


class ShowerTimeTodaySensor(_LogSensor):
    _attr_device_class = SensorDeviceClass.DURATION
    _attr_native_unit_of_measurement = UnitOfTime.MINUTES
    _attr_state_class = SensorStateClass.MEASUREMENT
    _attr_icon = "mdi:timer-outline"

    def __init__(self, manager) -> None:
        super().__init__(manager, "shower_time_today")

    @property
    def native_value(self):
        return self.manager.ledger.summary()["today_minutes"]


class ShowerTimeWeekSensor(_LogSensor):
    _attr_device_class = SensorDeviceClass.DURATION
    _attr_native_unit_of_measurement = UnitOfTime.MINUTES
    _attr_state_class = SensorStateClass.MEASUREMENT
    _attr_icon = "mdi:calendar-week"

    def __init__(self, manager) -> None:
        super().__init__(manager, "shower_time_week")

    @property
    def native_value(self):
        return self.manager.ledger.summary()["week_minutes"]

    @property
    def extra_state_attributes(self):
        s = self.manager.ledger.summary()
        return {"showers": s["week_showers"], "cost": s["week_cost"], "currency": self.manager.ledger.currency or None}


class LastShowerSensor(_LogSensor):
    _attr_device_class = SensorDeviceClass.DURATION
    _attr_native_unit_of_measurement = UnitOfTime.MINUTES
    _attr_icon = "mdi:shower-head"

    def __init__(self, manager) -> None:
        super().__init__(manager, "last_shower")

    @property
    def native_value(self):
        last = self.manager.ledger.summary()["last"]
        return round(last["minutes"]) if last else None

    @property
    def extra_state_attributes(self):
        last = self.manager.ledger.summary()["last"]
        if not last:
            return None
        return {**last, "currency": self.manager.ledger.currency or None,
                "log": self.manager.ledger.log[-20:]}


class ShowerCostTodaySensor(_LogSensor):
    """Cost of today's showers, at the price of the electricity that heated the water."""

    _attr_device_class = SensorDeviceClass.MONETARY
    _attr_icon = "mdi:cash"
    _attr_suggested_display_precision = 2

    def __init__(self, manager) -> None:
        super().__init__(manager, "shower_cost_today")

    @property
    def native_unit_of_measurement(self):
        return self.manager.ledger.currency or "€"

    @property
    def native_value(self):
        return self.manager.ledger.summary()["today_cost"]


class ReheatReadySensor(_LogSensor):
    """While the tank reheats: when it is full again (attribute: when there is enough for one shower)."""

    _attr_device_class = SensorDeviceClass.TIMESTAMP
    _attr_icon = "mdi:clock-check-outline"

    def __init__(self, manager) -> None:
        super().__init__(manager, "reheat_ready")

    @property
    def native_value(self):
        return self.manager.ledger.eta_full

    @property
    def extra_state_attributes(self):
        eta = self.manager.ledger.eta_one
        return {"one_shower_at": eta.isoformat(timespec="minutes") if eta else None}


class CurrentShowerSensor(_LogSensor):
    """Minutes of the shower going on right now (0 when nobody showers); attributes: cost so far."""

    _attr_device_class = SensorDeviceClass.DURATION
    _attr_native_unit_of_measurement = UnitOfTime.MINUTES
    _attr_suggested_display_precision = 0
    _attr_icon = "mdi:shower-head"

    def __init__(self, manager) -> None:
        super().__init__(manager, "current_shower")

    @property
    def native_value(self):
        r = self.manager.ledger.running(self.manager, dt_util.utcnow())
        return r["minutes"] if r else 0

    @property
    def extra_state_attributes(self):
        r = self.manager.ledger.running(self.manager, dt_util.utcnow())
        return {**(r or {}), "currency": self.manager.ledger.currency or None}


class ExpectedTodaySensor(ShowerTimeEntity, SensorEntity):
    """Shower minutes still expected today, from the household's habits; attributes: will the water last."""

    _attr_device_class = SensorDeviceClass.DURATION
    _attr_native_unit_of_measurement = UnitOfTime.MINUTES
    _attr_suggested_display_precision = 0
    _attr_icon = "mdi:calendar-clock"

    def __init__(self, manager) -> None:
        super().__init__(manager, "expected_today")

    @property
    def available(self) -> bool:
        return self.manager.forecast() is not None

    @property
    def native_value(self):
        f = self.manager.forecast()
        return round(f.expected_minutes) if f else None

    @property
    def extra_state_attributes(self):
        return {**(self.manager.forecast_attrs() or {}), "days_learned": self.manager.habits["days"]}


class _TotalSensor(_LogSensor):
    """Never-decreasing totals of logged showers, for Home Assistant's long-term statistics."""

    _attr_state_class = SensorStateClass.TOTAL_INCREASING
    key = ""

    @property
    def native_value(self):
        return self.manager.ledger.totals[self.key]


class ShowersTotalSensor(_TotalSensor):
    _attr_icon = "mdi:counter"
    key = "showers"

    def __init__(self, manager) -> None:
        super().__init__(manager, "showers_total")


class ShowerTimeTotalSensor(_TotalSensor):
    _attr_device_class = SensorDeviceClass.DURATION
    _attr_native_unit_of_measurement = UnitOfTime.MINUTES
    _attr_suggested_display_precision = 0
    _attr_icon = "mdi:timer-outline"
    key = "minutes"

    def __init__(self, manager) -> None:
        super().__init__(manager, "shower_time_total")


class ShowerEnergyTotalSensor(_TotalSensor):
    """Electricity used to heat the shower water (usable in the energy dashboard as an individual device)."""

    _attr_device_class = SensorDeviceClass.ENERGY
    _attr_native_unit_of_measurement = UnitOfEnergy.KILO_WATT_HOUR
    _attr_suggested_display_precision = 2
    _attr_icon = "mdi:lightning-bolt"
    key = "electricity_kwh"

    def __init__(self, manager) -> None:
        super().__init__(manager, "shower_energy_total")


class ShowerCostTotalSensor(_TotalSensor):
    _attr_device_class = SensorDeviceClass.MONETARY
    _attr_state_class = SensorStateClass.TOTAL
    _attr_suggested_display_precision = 2
    _attr_icon = "mdi:cash"
    key = "cost"

    def __init__(self, manager) -> None:
        super().__init__(manager, "shower_cost_total")

    @property
    def native_unit_of_measurement(self):
        return self.manager.ledger.currency or "€"


class LegionellaSensor(_LogSensor):
    """The last time the whole tank was at the legionella temperature."""

    _attr_device_class = SensorDeviceClass.TIMESTAMP
    _attr_icon = "mdi:bacteria-outline"

    def __init__(self, manager) -> None:
        super().__init__(manager, "legionella_last")

    @property
    def native_value(self):
        at = self.manager.legionella_at
        return dt_util.parse_datetime(at) if at else None

    @property
    def extra_state_attributes(self):
        m = self.manager
        return {"temperature": m.legionella_temp, "max_days": m.legionella_days, "overdue": m.legionella_overdue()}


class HeatLossSensor(ShowerTimeEntity, SensorEntity):
    """What the tank loses to its surroundings right now (W), from its measured heat-loss coefficient."""

    _attr_device_class = SensorDeviceClass.POWER
    _attr_native_unit_of_measurement = UnitOfPower.WATT
    _attr_state_class = SensorStateClass.MEASUREMENT
    _attr_suggested_display_precision = 0
    _attr_icon = "mdi:home-thermometer-outline"

    def __init__(self, manager) -> None:
        super().__init__(manager, "heat_loss")

    @property
    def available(self) -> bool:
        return self.manager.heat_loss_now() is not None

    @property
    def native_value(self):
        return self.manager.heat_loss_now()

    @property
    def extra_state_attributes(self):
        st = self.manager.loss
        last = self.manager.loss_samples[-1] if self.manager.loss_samples else None
        return {"coefficient_w_per_k": st.get("ua_recent"), "baseline_w_per_k": st.get("ua_baseline"),
                "ratio": st.get("ratio"), "raised": st.get("raised"), "leak_like": st.get("leak_like"),
                "measurements": st.get("measurements"), "last_measurement": last}


class OtherHotWaterTodaySensor(_LogSensor):
    """Hot water used today that was not a shower (washing up, taps, a bath being filled): heat taken from the
    tank; attributes: how often, electricity and cost."""

    _attr_device_class = SensorDeviceClass.ENERGY
    _attr_native_unit_of_measurement = UnitOfEnergy.KILO_WATT_HOUR
    _attr_suggested_display_precision = 2
    _attr_icon = "mdi:water-pump"

    def __init__(self, manager) -> None:
        super().__init__(manager, "other_hot_water_today")

    @property
    def native_value(self):
        return self.manager.ledger.other_today()["heat_kwh"]

    @property
    def extra_state_attributes(self):
        return {**self.manager.ledger.other_today(), "currency": self.manager.ledger.currency or None,
                "recent": self.manager.ledger.other[-10:]}
