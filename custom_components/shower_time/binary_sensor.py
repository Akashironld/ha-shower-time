"""Binary sensors: hot water almost gone, tank being reheated."""
from __future__ import annotations

from homeassistant.components.binary_sensor import BinarySensorDeviceClass, BinarySensorEntity
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from . import ShowerTimeConfigEntry
from .entity import ShowerTimeEntity


async def async_setup_entry(hass: HomeAssistant, entry: ShowerTimeConfigEntry,
                            async_add_entities: AddEntitiesCallback) -> None:
    m = entry.runtime_data
    ents = [LowSensor(m), ShoweringSensor(m), ShowerActiveSensor(m), EnoughTodaySensor(m), LegionellaOverdueSensor(m),
            HeatLossRaisedSensor(m)]
    if m.heating:
        ents.append(ReheatingSensor(m))
    async_add_entities(ents)


class LowSensor(ShowerTimeEntity, BinarySensorEntity):
    """On when less than the configured number of shower minutes is left."""

    _attr_icon = "mdi:water-alert"

    def __init__(self, manager) -> None:
        super().__init__(manager, "low")

    @property
    def is_on(self):
        e = self.manager.current
        return None if e is None else e.minutes < self.manager.low_minutes


class ReheatingSensor(ShowerTimeEntity, BinarySensorEntity):
    _attr_device_class = BinarySensorDeviceClass.HEAT
    _attr_icon = "mdi:water-boiler"

    def __init__(self, manager) -> None:
        super().__init__(manager, "reheating")

    @property
    def available(self) -> bool:
        return True

    @property
    def is_on(self):
        return self.manager.reheating


class ShoweringSensor(ShowerTimeEntity, BinarySensorEntity):
    """Hot water is being used right now (cold water coming into the tank), not while reheating."""

    _attr_device_class = BinarySensorDeviceClass.RUNNING
    _attr_icon = "mdi:shower-head"

    def __init__(self, manager) -> None:
        super().__init__(manager, "showering")

    @property
    def available(self) -> bool:
        return True

    @property
    def is_on(self):
        return self.manager.showering and self.manager.use_kind() != "coil"

    @property
    def extra_state_attributes(self):
        return {"kind": self.manager.use_kind()}


class ShowerActiveSensor(ShowerTimeEntity, BinarySensorEntity):
    """On while someone showers: hot water in use that was recognised as a shower (by its heat, or right away
    with a bathroom signal). Washing up and taps do not turn it on."""

    _attr_device_class = BinarySensorDeviceClass.RUNNING
    _attr_icon = "mdi:shower"

    def __init__(self, manager) -> None:
        super().__init__(manager, "shower_active")

    @property
    def available(self) -> bool:
        return True

    @property
    def is_on(self):
        return self.manager.use_kind() == "shower"


class EnoughTodaySensor(ShowerTimeEntity, BinarySensorEntity):
    """On when the hot water is expected to last for today's usual showers (without a reheat in between)."""

    _attr_icon = "mdi:water-check"

    def __init__(self, manager) -> None:
        super().__init__(manager, "enough_today")

    @property
    def available(self) -> bool:
        return self.manager.forecast() is not None

    @property
    def is_on(self):
        f = self.manager.forecast()
        return f.enough if f else None

    @property
    def extra_state_attributes(self):
        return self.manager.forecast_attrs()


class LegionellaOverdueSensor(ShowerTimeEntity, BinarySensorEntity):
    """On (problem) when the whole tank has not been at the legionella temperature for too long."""

    _attr_device_class = BinarySensorDeviceClass.PROBLEM
    _attr_icon = "mdi:bacteria-outline"

    def __init__(self, manager) -> None:
        super().__init__(manager, "legionella_overdue")

    @property
    def available(self) -> bool:
        return self.manager.legionella_overdue() is not None

    @property
    def is_on(self):
        return self.manager.legionella_overdue()


class HeatLossRaisedSensor(ShowerTimeEntity, BinarySensorEntity):
    """On (problem) when the tank loses clearly more heat than it used to: insulation or a dripping hot tap."""

    _attr_device_class = BinarySensorDeviceClass.PROBLEM
    _attr_icon = "mdi:water-alert-outline"

    def __init__(self, manager) -> None:
        super().__init__(manager, "heat_loss_raised")

    @property
    def available(self) -> bool:                # a drip is seen from the first nights, the trend after ~11 days
        st = self.manager.loss
        return bool(st.get("ready") or self.manager.drip_nights)

    @property
    def is_on(self):
        st = self.manager.loss
        return bool(st.get("raised") or st.get("leak_like"))

    @property
    def extra_state_attributes(self):
        st = self.manager.loss
        return {"leak_like": st.get("leak_like"), "steady_drip_at_night": st.get("dripping"), "ratio": st.get("ratio"),
                "coefficient_w_per_k": st.get("ua_recent"), "baseline_w_per_k": st.get("ua_baseline")}
