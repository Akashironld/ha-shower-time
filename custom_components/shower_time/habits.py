"""Habits of the household and of the tank: when showers are taken (per weekday and hour) and when the heat pump
reheats, today's forecast from them, and when the whole tank last reached the legionella temperature. ``Habits`` and
``Legionella`` own that state and reach the rest of the integration only through their host protocols; reading the
long-term statistics stays in the manager, which hands them to ``Legionella.from_statistics``."""
from __future__ import annotations

from datetime import date, datetime, timedelta, timezone
from typing import Any, Protocol

from homeassistant.core import callback
from homeassistant.util import dt as dt_util

from . import insights, model
from .const import LOG_DAYS


class HabitsHost(Protocol):
    ledger: Any                                 # ledger.ShowerLedger: the shower log and its coverage
    current: model.Estimate | None              # as shown (minus the live countdown)
    bottom: str | None
    reheating: bool
    params: dict

    def ref_minutes(self) -> float | None: ...
    def loss_k_h(self) -> float: ...
    def request_save(self) -> None: ...


class Habits:
    """The shower and reheat profiles, and the log of reheat hours they are built from."""

    def __init__(self, host: HabitsHost) -> None:
        self.h = host
        self.habits: dict = insights.new_habits()
        self.reheat_log: dict[str, list[int]] = {}  # local day -> hours in which a reheat started
        self.reheat_since: str | None = None        # first day the reheat log is complete from
        self._last_reheat_start: datetime | None = None

    @callback
    def update(self) -> None:
        """Add every completed day the shower log fully covers to the habit profile."""
        cov = self.h.ledger.covered_from
        if not cov:
            return
        today = dt_util.now().date()
        day = max(date.fromisoformat(cov), today - timedelta(days=LOG_DAYS - 1))
        if self.habits.get("last_day"):
            day = max(day, date.fromisoformat(self.habits["last_day"]) + timedelta(days=1))
        by_day: dict[date, list[tuple[float, float]]] = {}
        for e in self.h.ledger.log:
            t = dt_util.as_local(datetime.fromisoformat(e["start"]))
            by_day.setdefault(t.date(), []).append((t.hour + t.minute / 60, e["minutes"]))
        changed = False
        while day < today:
            if insights.is_incomplete(self.habits, day.isoformat()):
                self.habits["last_day"] = day.isoformat()          # showers may have been missed: skip the day
            else:
                insights.add_day(self.habits, day.weekday(), day.isoformat(), insights.minutes_by_hour(by_day.get(day, [])))
            day += timedelta(days=1)
            changed = True
        if changed:
            self.h.request_save()
        self.update_reheat_habits()

    @callback
    def log_reheat(self, now: datetime) -> None:
        if self._last_reheat_start is not None and now - self._last_reheat_start < timedelta(hours=1):
            return                                          # one reheat in pieces counts once
        self._last_reheat_start = now
        local = dt_util.as_local(now)
        day = local.date().isoformat()
        self.reheat_log[day] = sorted(set(self.reheat_log.get(day, [])) | {local.hour})
        if self.reheat_since is None:
            self.reheat_since = (local.date() + timedelta(days=1)).isoformat()
        self.prune_reheat_log()
        self.h.request_save()

    def prune_reheat_log(self) -> None:
        cutoff = (dt_util.now().date() - timedelta(days=LOG_DAYS + 6)).isoformat()
        self.reheat_log = {d: h for d, h in self.reheat_log.items() if d >= cutoff}

    @callback
    def update_reheat_habits(self) -> None:
        """Add every completed day since the reheat log is complete to the reheat profile."""
        if not self.reheat_since:
            return
        today = dt_util.now().date()
        r = self.habits.get("reheat") or {}
        day = date.fromisoformat(self.reheat_since)
        if r.get("last_day"):
            day = max(day, date.fromisoformat(r["last_day"]) + timedelta(days=1))
        day = max(day, today - timedelta(days=LOG_DAYS + 5))
        changed = False
        while day < today:
            iso = day.isoformat()
            if not insights.is_incomplete(self.habits, iso):
                insights.add_reheat_day(self.habits, self.reheat_log.get(iso, []))
            self.habits.setdefault("reheat", {"days": 0, "hours": [0.0] * 24})["last_day"] = iso
            day += timedelta(days=1)
            changed = True
        if changed:
            self.h.request_save()

    def forecast(self) -> insights.Forecast | None:
        """Will the hot water last today (without a reheat in between)? None until a week of habits is known."""
        e = self.h.current
        if e is None or not self.h.bottom or not insights.habits_ready(self.habits):
            return None                             # without a bottom sensor showers are not seen
        minutes = e.minutes
        if self.h.reheating:                          # being filled: count on what it holds when full
            minutes = max(minutes, self.h.ref_minutes() or 0.0)
        now = dt_util.now()
        return insights.forecast(self.habits, weekday=now.weekday(), hour=now.hour + now.minute / 60,
                                 minutes_now=minutes, per_minute=e.per_minute, loss_k_h=self.h.loss_k_h(),
                                 typical=self.h.params["duration"]["v"])

    def forecast_attrs(self) -> dict | None:
        """Today's forecast with clock times instead of hours (for attributes and notifications)."""
        f = self.forecast()
        if f is None:
            return None
        now = dt_util.now()
        day = now.replace(hour=0, minute=0, second=0, microsecond=0)
        return {"enough": f.enough, "expected_minutes": f.expected_minutes, "available_minutes": f.available_minutes,
                "reheat_chance": f.reheat_chance,
                "runs_out_at": (day + timedelta(hours=f.runs_out_at)).isoformat(timespec="minutes")
                if f.runs_out_at is not None else None,
                "next_shower_at": (now + timedelta(hours=f.next_shower_at)).replace(second=0, microsecond=0)
                .isoformat(timespec="minutes") if f.next_shower_at is not None else None}


class LegionellaHost(Protocol):
    top: str
    bottom: str | None
    top_value: float | None
    bottom_value: float | None

    def request_save(self) -> None: ...


class Legionella:
    """When the whole tank (top and bottom) last reached the legionella temperature, and whether that is overdue."""

    def __init__(self, host: LegionellaHost, temp: float, days: float) -> None:
        self.h = host
        self.temp = temp
        self.days = days
        self.at: str | None = None
        self.checked = False                        # the history was searched (so "never" means never)
        self.since: str | None = None               # how far back we can tell: the oldest statistic, or when we began

    @callback
    def track(self) -> None:
        t, b = self.h.top_value, self.h.bottom_value
        if t is not None and t >= self.temp and (self.h.bottom is None or (b is not None and b >= self.temp)):
            stamp = dt_util.now().isoformat(timespec="minutes")
            if stamp != self.at:
                self.at = stamp
                self.checked = True
                self.h.request_save()

    def from_statistics(self, stats: dict) -> None:
        """The hour statistics (max) of the tank sensors: how far back they reach, and the last hour in which
        the whole tank reached the legionella temperature."""
        def by_hour(ent: str) -> dict[float, float]:
            out = {}
            for r in stats.get(ent, []):
                ts = r["start"]
                if r.get("max") is not None:
                    out[ts.timestamp() if isinstance(ts, datetime) else float(ts)] = float(r["max"])
            return out
        top = by_hour(self.h.top)
        bot = by_hour(self.h.bottom) if self.h.bottom else None
        if not top:
            return                              # no long-term statistics: wait for the live readings
        # "no hit" only means something for as long as the statistics reach back (of both sensors)
        first = max(min(top), min(bot)) if bot else min(top)
        old = dt_util.parse_datetime(self.since) if self.since else None
        if old is None or first < old.timestamp():
            self.since = datetime.fromtimestamp(first, timezone.utc).isoformat()
            self.h.request_save()
        hits = [t for t, v in top.items() if v >= self.temp
                and (bot is None or bot.get(t, -99.0) >= self.temp)]
        if hits and self.at is None:
            self.at = dt_util.as_local(datetime.fromtimestamp(max(hits) + 3599, timezone.utc)).isoformat(timespec="minutes")
            self.h.request_save()
        self.checked = True

    def overdue(self) -> bool | None:
        if not self.checked:
            return None
        if self.at is None:
            # no heat-up found: overdue only once we could have seen one for the whole period (a tank with a few
            # days of statistics, or just installed, has not been proven overdue)
            since = dt_util.parse_datetime(self.since) if self.since else None
            if since is None or dt_util.utcnow() - since < timedelta(days=self.days):
                return None
            return True
        age = dt_util.now() - datetime.fromisoformat(self.at)
        return age > timedelta(days=self.days)
