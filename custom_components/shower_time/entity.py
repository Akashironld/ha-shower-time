"""Shared base entity."""
from __future__ import annotations

from homeassistant.helpers.device_registry import DeviceEntryType, DeviceInfo
from homeassistant.helpers.entity import Entity

from .const import DOMAIN
from .manager import ShowerTimeManager


class ShowerTimeEntity(Entity):
    _attr_has_entity_name = True
    _attr_should_poll = False

    def __init__(self, manager: ShowerTimeManager, key: str) -> None:
        self.manager = manager
        self._attr_translation_key = key
        self._attr_unique_id = f"{manager.entry.entry_id}_{key}"
        self._attr_device_info = DeviceInfo(
            identifiers={(DOMAIN, manager.entry.entry_id)},
            name=manager.entry.title,
            manufacturer="Hot Water Shower Time",
            model="Self-learning hot water model",
            entry_type=DeviceEntryType.SERVICE,
        )

    async def async_added_to_hass(self) -> None:
        self.async_on_remove(self.manager.add_listener(self.async_write_ha_state))

    @property
    def available(self) -> bool:
        return self.manager.estimate is not None
