"""Event entity: a shower started or ended (with its minutes and cost), for automations."""
from __future__ import annotations

from homeassistant.components.event import EventEntity
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from . import ShowerTimeConfigEntry
from .entity import ShowerTimeEntity


async def async_setup_entry(hass: HomeAssistant, entry: ShowerTimeConfigEntry,
                            async_add_entities: AddEntitiesCallback) -> None:
    async_add_entities([ShowerEvent(entry.runtime_data)])


class ShowerEvent(ShowerTimeEntity, EventEntity):
    """shower_started when hot water use begins; shower_ended for a finished shower (4 to 40 minutes),
    with minutes, heat_kwh, electricity_kwh, cost and currency."""

    _attr_event_types = ["shower_started", "shower_ended"]
    _attr_icon = "mdi:shower-head"

    def __init__(self, manager) -> None:
        super().__init__(manager, "shower")

    @property
    def available(self) -> bool:
        return True

    async def async_added_to_hass(self) -> None:
        await super().async_added_to_hass()
        self.async_on_remove(self.manager.add_event_listener(self._on_event))

    @callback
    def _on_event(self, kind: str, data: dict) -> None:
        self._trigger_event(kind, data)
        self.async_write_ha_state()
