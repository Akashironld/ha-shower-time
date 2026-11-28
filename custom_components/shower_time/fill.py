"""The fill level: 100 % is the tank right after the last complete reheat (its minutes, average and readings), and
what went where since then (showered, other hot water, cooling, top-ups). One per tank (``FillLevel``); it owns that
state and reaches the rest of the integration only through ``FillHost``. Reading the history to find the last reheat
stays in the manager, which hands the points to ``from_history``."""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Any, Protocol

from homeassistant.core import callback
from homeassistant.util import dt as dt_util

from . import insights, model

FULLS_KEPT = 7                  # daily reheats kept in ``FillLevel.fulls``
FULLS_MEDIAN = 5                # the last ones that make the usual full tank


class FillHost(Protocol):
    """What the fill level reads from and does through the rest of the integration (the manager)."""
    estimate: model.Estimate | None
    current: model.Estimate | None              # as shown (minus the live countdown)
    top_value: float | None
    bottom_value: float | None
    heating: str | None                         # the reheat entity, when there is one
    reheating: bool
    params: dict
    volume: float
    shower_temp: float
    ema_now: float | None
    blurred_minutes: float
    ledger: Any                                 # ledger.ShowerLedger
    use: Any                                    # insights.UseSession | None, the use going on

    def draw_flow(self) -> float | None: ...
    def use_kind(self) -> str | None: ...
    def shower_stopped_at(self) -> datetime | None: ...
    def loss_k_h(self) -> float: ...
    def log_reheat(self, now: datetime) -> None: ...
    def save_later(self, delay: float) -> None: ...
    def request_save(self) -> None: ...


class FillLevel:
    """100 % and what happened since the last complete reheat."""

    def __init__(self, host: FillHost) -> None:
        self.h = host
        # 100 % = the shower minutes right after the last reheat; the fill level drops with use and standing loss
        self.full_ref: float | None = None          # minutes at the last reheat (kept for display/compat)
        self.full_ref_avg: float | None = None      # tank average temperature right after the last reheat
        self.full_ref_state: list[float] | None = None   # top and bottom right after the last reheat
        self.full_ref_at: str | None = None
        self.top_ups: list[dict] = []           # partial reheats since the last complete one
        # the tank after recent daily reheats (``at``, ``state`` [top, bottom], ``avg``): what a full tank usually is
        self.fulls: list[dict] = []
        self._heat_from: tuple[datetime, float] | None = None
        self._ref_low: float | None = None
        self._last_minutes: float | None = None
        self._rise_at: datetime | None = None      # without a reheat entity: when the minutes last rose like a reheat
        self._was_heating = False                   # the tank was being reheated at the last estimate

    @property
    def was_heating(self) -> bool:
        return self._was_heating

    def load(self, stored: dict) -> None:
        self.full_ref_avg = stored.get("full_ref_avg")
        self.full_ref_state = stored.get("full_ref_state")
        self.full_ref_at = stored.get("full_ref_at")
        self.top_ups = stored.get("top_ups") or []
        self.fulls = [x for x in stored.get("fulls") or [] if isinstance(x, dict)][-FULLS_KEPT:]
        # a reference in minutes only (before 0.5.1) depends on the model it was computed with: rebuild it
        self.full_ref = stored.get("full_ref") if self.full_ref_avg is not None else None

    def from_history(self, points: list) -> bool:
        """The last reheat in the history ([ms, minutes, average, top, bottom]), with the rules of ``track``
        replayed: 100 % and the readings then. True when a reference was set (then save and update)."""
        ref = low = prev = ts = avg = state = None
        for t, v, a, top, bot in points:
            if ref is None:
                ref = low = prev = v
                ts, avg, state = t, a, [top, bot]
                continue
            low = min(low, v)
            if (v > prev and v >= low + 3) or v > ref:
                ref, ts, low, avg, state = v, t, v, a, [top, bot]
            prev = v
        self.full_ref_state = state if state and None not in state else None
        e = self.h.estimate
        if ref is None and e:
            ref = e.minutes
        if ref is None:
            return False
        self.full_ref = float(ref)
        self.full_ref_avg = avg if avg is not None else (e.empty_at + float(ref) * e.per_minute if e else None)
        self.full_ref_at = (dt_util.as_local(datetime.fromtimestamp(ts / 1000, timezone.utc)).isoformat(timespec="seconds")
                            if ts else dt_util.now().isoformat(timespec="seconds"))
        self._ref_low = e.minutes if e else ref
        return True

    def set_ref(self, minutes: float) -> None:
        self.full_ref = minutes
        if self.h.estimate is not None:
            self.full_ref_avg = self.h.estimate.average
        # the readings themselves: with the layer model the tank average alone does not give back the minutes
        self.full_ref_state = ([self.h.top_value, self.h.bottom_value]
                               if self.h.top_value is not None and self.h.bottom_value is not None else None)
        self.full_ref_at = dt_util.now().isoformat(timespec="seconds")
        self.top_ups = []
        self._ref_low = minutes
        self._note_full()
        self.h.save_later(60)

    def _note_full(self) -> None:
        """Keep the tank as it is now in ``fulls``, one per day: the same day as the last one, or within two hours
        of it (a reheat in two parts, the sensors still settling past midnight), it replaces that one."""
        now = dt_util.now()
        state = ([self.h.top_value, self.h.bottom_value]
                 if self.h.top_value is not None and self.h.bottom_value is not None else None)
        avg = self.h.estimate.average if self.h.estimate is not None else None
        if state is None and avg is None:
            return
        rec = {"at": now.isoformat(timespec="seconds"), "state": state, "avg": avg}
        last = dt_util.parse_datetime(self.fulls[-1].get("at") or "") if self.fulls else None
        if last is not None and (dt_util.as_local(last).date() == now.date() or now - last < timedelta(hours=2)):
            self.fulls[-1] = rec
        else:
            self.fulls = (self.fulls + [rec])[-FULLS_KEPT:]

    def typical_full(self) -> float | None:
        """The minutes a full tank usually holds: the median of the last few daily reheats, with the *current*
        model. None until there are three."""
        mins = sorted(v for v in (self._minutes_of(x.get("state"), x.get("avg"), None) for x in self.fulls[-FULLS_MEDIAN:])
                      if v is not None)
        if len(mins) < 3:
            return None
        n = len(mins)
        return mins[n // 2] if n % 2 else (mins[n // 2 - 1] + mins[n // 2]) / 2

    @callback
    def track(self, m: float) -> None:
        """Keep 100 % at the end of the latest *complete* reheat.

        A reheat that leaves the tank evenly hot (top and bottom within 2.5 °C) and about as full as last time
        (90 %) sets a new 100 %; so does one the day after (20+ hours) that heats to a lower temperature, when it
        is about as full (90 %) as the tank usually is after a daily reheat: the median of the last five (smart
        controllers vary the target, and a legionella heat-up is not the usual full). A short reheat the day after
        a normal full tank (#10: to 45 °C after 56 °C) is a top-up; a lasting lower target is the usual full after
        three days. Anything less is a top-up: it is kept in ``top_ups`` (when, how much) and "since reheat" goes
        on from the last complete reheat. A top-up never becomes the new 100 % unless it beats the old one.
        Reheating is taken from the reheat entity, or recognised from the minutes rising 3+ above the lowest
        point since the last reheat; it ends when the entity switches off or the rise stops for 20 minutes."""
        now = dt_util.utcnow()
        prev = self._last_minutes
        self._last_minutes = m
        if self.full_ref is None:
            return                              # still being initialised from the history
        ref_now = self.ref_minutes() or self.full_ref
        if self._ref_low is None or m < self._ref_low:
            self._ref_low = m
        if self.h.heating:
            heating = self.h.reheating
        else:
            rising = prev is not None and m > prev + 0.2
            if rising and m >= self._ref_low + 3:
                self._rise_at = now
            heating = self._rise_at is not None and (now - self._rise_at).total_seconds() < 1200
        was = self._was_heating
        self._was_heating = heating
        if heating and not was:
            self.h.log_reheat(now)
            self._heat_from = (now, prev if prev is not None else m)
        if was and not heating:                 # a reheat just finished
            even = self.h.bottom_value is None or (self.h.top_value is not None and self.h.top_value - self.h.bottom_value <= 2.5)
            since = dt_util.parse_datetime(self.full_ref_at) if self.full_ref_at else None
            old = since is None or now - since > timedelta(hours=20)
            typical = None
            if even and old and m >= 0.5 * ref_now:
                self._note_full()               # a daily reheat: part of what a full tank usually is
                typical = self.typical_full()
            if m > ref_now or (even and (m >= 0.9 * ref_now or (old and (typical is None or m >= 0.9 * typical)))):
                self.set_ref(m)
            elif self._heat_from is not None:
                start, before = self._heat_from
                self.top_ups = (self.top_ups + [{
                    "at": dt_util.as_local(start).isoformat(timespec="minutes"),
                    "until": dt_util.as_local(now).isoformat(timespec="minutes"),
                    "added_minutes": round(max(0.0, m - before), 1),
                    "top": round(self.h.top_value, 1) if self.h.top_value is not None else None}])[-10:]
                self.h.request_save()
            self._heat_from = None
            self._rise_at = None
            return
        # sensors still settling after the reheat state switched off. Not for less than a minute: with the layer
        # model a full tank's minutes rise a little when the top reads 0.1 K lower before the bottom updates
        if not heating and m > ref_now + 1.0:
            self.set_ref(m)

    def ref_minutes(self) -> float | None:
        """Minutes the tank held right after the last reheat, with the *current* model (so the fill level does
        not jump when the model learns or the cold water estimate changes)."""
        return self._minutes_of(self.full_ref_state, self.full_ref_avg, self.full_ref)

    def _minutes_of(self, state: list | None, avg: float | None, fallback: float | None) -> float | None:
        """Minutes a tank with these readings (or this average) holds, with the current model."""
        e = self.h.estimate
        if e is None:
            return fallback
        if state and None not in state and self.h.params.get("geo"):
            # layer model: the minutes in the tank as it was then (its empty point moves with the tank's state)
            top, bottom = state
            now = dt_util.now()
            return model.estimate(self.h.params, top, bottom, volume=self.h.volume, shower_temp=self.h.shower_temp,
                                  month=now.month, doy=now.timetuple().tm_yday, ema=self.h.ema_now, hot=top,
                                  draw_flow=self.h.draw_flow()).minutes
        if avg is not None and e.per_minute > 0:
            return max(0.0, (avg - e.empty_at) / e.per_minute)
        return fallback

    def since_reheat(self) -> dict | None:
        """Where the hot water went since the last complete reheat: left, showered, other hot water (a bath, the
        sink) and lost to cooling, plus what a reheat in between added.

        Showers count in minutes (one shower minute takes about one minute off the estimate), other hot water by
        the heat it took. Cooling is the tank's standing loss over the time since the reheat, or the drop that is
        left unexplained when that is more. When the tank holds more than that adds up to and a reheat was seen in
        between, it put heat back in: that is ``reheated_minutes``. Without a reheat the uses are cut back to what
        the tank lost (``overcounted_minutes``). Left + showered + other + cooling = reference + reheated."""
        e, ref = self.h.current, self.ref_minutes()
        if e is None or not ref or not self.full_ref_at or e.per_minute <= 0:
            return None
        try:
            since = dt_util.parse_datetime(self.full_ref_at)
        except (TypeError, ValueError):
            return None
        if since is None:
            return None
        hours = max(0.0, (dt_util.now() - since).total_seconds() / 3600)
        key = dt_util.as_local(since).isoformat(timespec="minutes")
        showered = sum(x["minutes"] for x in self.h.ledger.showers() if x["start"] >= key)
        per_min_kwh = e.per_minute * self.h.volume * model.CP
        # a bath is a session of its own, but not a shower: it stays with the tap and bucket it was while it ran (#10)
        bath = sum(x["heat_kwh"] for x in self.h.ledger.baths() if x["start"] >= key) / per_min_kwh
        other = bath + sum(x["heat_kwh"] for x in self.h.ledger.other if x["start"] >= key) / per_min_kwh
        kind = self.h.use_kind()
        if kind == "shower" and self.h.ledger.session_start:
            until = self.h.shower_stopped_at() or dt_util.utcnow()      # the meter saw the shower stop: no further
            showered += max(0.0, (until - self.h.ledger.session_start).total_seconds() / 60)
        elif kind not in (None, "coil") and self.h.use is not None:
            other += self.h.use.drawn / e.per_minute       # a bath or tap running now: the heat it took so far
        standing = self.h.loss_k_h() * hours / e.per_minute
        heating_now = self._was_heating and self._heat_from is not None   # a top-up going on now
        s = insights.split_since_reheat(ref, e.minutes, showered, other, standing, bool(self.top_ups) or heating_now)
        cooling, reheated = s["cooling"], s["reheated"]
        # hot water that turned lukewarm while the tank stood counts as cooled minutes, but its heat is still there
        heat = max(min(cooling, standing), cooling - self.h.blurred_minutes) * per_min_kwh
        price = self.h.ledger.tank_price
        if price is None:
            p, _ = self.h.ledger.price_now(self.h)
            price = p / self.h.ledger.cop(self.h) if p is not None else None
        last_top_up = self.top_ups[-1]["at"] if self.top_ups else None
        if heating_now:
            last_top_up = dt_util.as_local(self._heat_from[0]).isoformat(timespec="minutes")
        return {"since": self.full_ref_at, "hours": round(hours, 1), "reference_minutes": round(ref, 1),
                "top_ups": self.top_ups, "last_top_up_at": last_top_up,
                "remaining_minutes": round(e.minutes, 1), "showered_minutes": round(s["showered"], 1),
                "other_minutes": round(s["other"], 1),
                "bath_minutes": round(bath * s["other"] / other, 1) if other else 0.0,   # its share of other
                "cooling_minutes": round(cooling, 1), "lukewarm_minutes": round(min(cooling, self.h.blurred_minutes), 1),
                "reheated_minutes": round(reheated, 1), "total_minutes": round(ref + reheated, 1),
                "overcounted_minutes": round(s["overcounted"], 1),
                "cooling_kwh_heat": round(heat, 2),
                "cooling_cost": round(heat * price, 2) if price is not None else None}

    def standing_loss_per_day(self) -> dict:
        """What standing loss costs per day (heat, electricity, money) with the learned loss rate."""
        heat = self.h.params["loss"]["v"] * 24 * self.h.volume * model.CP
        elec = heat / self.h.ledger.cop(self.h)
        price = self.h.ledger.tank_price
        if price is None:
            p, _ = self.h.ledger.price_now(self.h)
            price = p / self.h.ledger.cop(self.h) if p is not None else None
        return {"kwh_heat": round(heat, 2), "kwh_electricity": round(elec, 2),
                "cost": round(heat * price, 2) if price is not None else None,
                "cost_year": round(heat * price * 365) if price is not None else None}

    def fill_percent(self) -> int | None:
        e, ref = self.h.current, self.ref_minutes()
        if e is None or not ref or ref <= 0:
            return None
        return int(round(max(0.0, min(100.0, 100 * e.minutes / ref))))
