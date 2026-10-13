"""Button: learn from the history now instead of waiting for the night."""
from __future__ import annotations

from homeassistant.components.button import ButtonEntity
from homeassistant.const import EntityCategory
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from . import ShowerTimeConfigEntry
from .entity import ShowerTimeEntity


async def async_setup_entry(hass: HomeAssistant, entry: ShowerTimeConfigEntry,
                            async_add_entities: AddEntitiesCallback) -> None:
    async_add_entities([LearnButton(entry.runtime_data)])


class LearnButton(ShowerTimeEntity, ButtonEntity):
    _attr_entity_category = EntityCategory.CONFIG
    _attr_icon = "mdi:school"

    def __init__(self, manager) -> None:
        super().__init__(manager, "learn_now")

    @property
    def available(self) -> bool:
        return True

    async def async_press(self) -> None:
        await self.manager.async_learn()
