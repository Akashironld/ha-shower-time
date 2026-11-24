"""Health: Home Assistant repairs for a tank sensor that stays away, sensors that look swapped, learning that keeps
failing, a possible leak or raised heat loss; the problems the card shows; and the time Home Assistant was off (the
habits skip those days). ``Health`` owns that state and reaches the rest of the integration through ``HealthHost``."""
from __future__ import annotations

from datetime import datetime, timedelta
from typing import Any, Protocol

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers import issue_registry as ir
from homeassistant.util import dt as dt_util

from . import insights
from .const import DOMAIN


class HealthHost(Protocol):
    hass: HomeAssistant
    entry: ConfigEntry
    top: str
    bottom: str | None
    top_value: float | None
    bottom_value: float | None
    reheating: bool
    learn_failures: int
    loss: dict                                  # heat-loss status (insights.loss_status)
    habits: dict                                # the habit profile (days Home Assistant was off are marked in it)
    legionella: Any                             # habits.Legionella

    def request_save(self) -> None: ...


class Health:
    def __init__(self, host: HealthHost) -> None:
        self.h = host
        self._missing_since: dict[str, datetime] = {}
        self._swap_since: datetime | None = None
        self.alive_at: str | None = None            # saved every 10 minutes: shows when Home Assistant was off

    def _issue_id(self, key: str) -> str:
        return f"{self.h.entry.entry_id}_{key.replace(':', '_')}"

    @callback
    def _issue(self, key: str, active: bool, placeholders: dict | None = None) -> None:
        if active:
            ir.async_create_issue(self.h.hass, DOMAIN, self._issue_id(key), is_fixable=False,
                                  severity=ir.IssueSeverity.WARNING, translation_key=key.split(":")[0],
                                  translation_placeholders={"name": self.h.entry.title, **(placeholders or {})})
        else:
            ir.async_delete_issue(self.h.hass, DOMAIN, self._issue_id(key))

    @callback
    def note_downtime(self) -> None:
        """Home Assistant was off for a while: the days in between may miss showers, so the habits skip them."""
        if self.alive_at:
            last = dt_util.parse_datetime(self.alive_at)
            if last is not None and dt_util.utcnow() - last > timedelta(hours=1):
                day, today = dt_util.as_local(last).date(), dt_util.now().date()
                days = []
                while day <= today:
                    days.append(day.isoformat())
                    day += timedelta(days=1)
                insights.mark_incomplete(self.h.habits, days)
        self.alive_at = dt_util.utcnow().isoformat()

    def problems(self) -> list[dict]:
        """What needs attention, for the card: a possible leak, raised heat loss, legionella, a silent sensor."""
        out = []
        if self.h.loss.get("leak_like"):
            out.append({"key": "leak"})
        elif self.h.loss.get("raised"):
            out.append({"key": "heat_loss", "ratio": self.h.loss.get("ratio")})
        if self.h.legionella.overdue():
            days = None
            if self.h.legionella.at:
                days = (dt_util.now() - datetime.fromisoformat(self.h.legionella.at)).days
            out.append({"key": "legionella", "days": days})
        now = dt_util.utcnow()
        if any(now - t >= timedelta(hours=2) for t in self._missing_since.values()):
            out.append({"key": "sensor"})
        return out

    @callback
    def check(self, _now=None) -> None:
        now = dt_util.utcnow()
        self.alive_at = now.isoformat()
        self.h.request_save()
        for key, ent, val in (("top", self.h.top, self.h.top_value), ("bottom", self.h.bottom, self.h.bottom_value)):
            if not ent:
                self._issue(f"sensor_missing:{key}", False)     # a sensor emptied under Configure
                continue
            if val is None:
                since = self._missing_since.setdefault(key, now)
                self._issue(f"sensor_missing:{key}", now - since >= timedelta(hours=2), {"entity": ent})
                if now - since >= timedelta(hours=2):        # showers may be missed today
                    insights.mark_incomplete(self.h.habits, [dt_util.now().date().isoformat()])
            else:
                self._missing_since.pop(key, None)
                self._issue(f"sensor_missing:{key}", False)
        t, b = self.h.top_value, self.h.bottom_value
        swapped = t is not None and b is not None and b > t + 5 and not self.h.reheating
        self._swap_since = (self._swap_since or now) if swapped else None
        self._issue("sensors_swapped", bool(self._swap_since and now - self._swap_since >= timedelta(hours=6)),
                    {"top": self.h.top, "bottom": self.h.bottom or ""})
        self._issue("learning_failed", self.h.learn_failures >= 3, {"nights": str(self.h.learn_failures)})

    @callback
    def sync_loss_issues(self) -> None:
        st = self.h.loss
        ph = {"baseline": f"{st['ua_baseline']:.2f}" if st.get("ua_baseline") else "-",
              "recent": f"{st['ua_recent']:.2f}" if st.get("ua_recent") else "-",
              "ratio": f"{st['ratio']:.1f}" if st.get("ratio") else "-"}
        self._issue("possible_leak", bool(st.get("leak_like")), ph)
        self._issue("heat_loss_raised", bool(st.get("raised")) and not st.get("leak_like"), ph)
