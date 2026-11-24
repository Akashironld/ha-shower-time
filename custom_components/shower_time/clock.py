"""Time as the integration counts it: minutes since the epoch (how the signals and the history keep time)."""
from __future__ import annotations

from datetime import datetime, timezone

from homeassistant.util import dt as dt_util


def at(minute: float) -> datetime:
    """A time in minutes since the epoch as a UTC datetime."""
    return datetime.fromtimestamp(minute * 60, timezone.utc)


def now_min() -> float:
    """Now, in minutes since the epoch."""
    return dt_util.utcnow().timestamp() / 60
