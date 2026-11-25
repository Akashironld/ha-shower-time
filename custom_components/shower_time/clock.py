"""Time as the integration counts it, without Home Assistant: minutes since the epoch (how the signals and the
history keep time), and the clock the pure parts are given (``Clock``). The manager hands them Home Assistant's clock
(its time zone, and the time the tests freeze); a test without Home Assistant uses ``FixedZoneClock``."""
from __future__ import annotations

from datetime import datetime, timezone, tzinfo
from typing import Protocol


def at(minute: float) -> datetime:
    """A time in minutes since the epoch as a UTC datetime."""
    return datetime.fromtimestamp(minute * 60, timezone.utc)


class Clock(Protocol):
    def utcnow(self) -> datetime: ...
    def now(self) -> datetime: ...                          # local, aware
    def as_local(self, t: datetime) -> datetime: ...
    def parse(self, text: str) -> datetime | None: ...      # an ISO time as stored (aware)


def now_min(clock: Clock) -> float:
    """Now, in minutes since the epoch."""
    return clock.utcnow().timestamp() / 60


class FixedZoneClock:
    """A clock in one time zone, for running the pure parts without Home Assistant (tests, the bench). ``now_fn``
    gives the UTC time (a test moves it on)."""

    def __init__(self, tz: tzinfo, now_fn=None) -> None:
        self.tz = tz
        self._now_fn = now_fn or (lambda: datetime.now(timezone.utc))

    def utcnow(self) -> datetime:
        return self._now_fn()

    def now(self) -> datetime:
        return self.utcnow().astimezone(self.tz)

    def as_local(self, t: datetime) -> datetime:
        return t.astimezone(self.tz) if t.tzinfo else t.replace(tzinfo=timezone.utc).astimezone(self.tz)

    def parse(self, text: str) -> datetime | None:
        try:
            t = datetime.fromisoformat(text)
        except (TypeError, ValueError):
            return None
        return t if t.tzinfo else t.replace(tzinfo=self.tz)
