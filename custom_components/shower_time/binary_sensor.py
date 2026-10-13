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
    ents = [LowSensor(m), ShoweringSensor(m)]
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
        return self.manager.showering
