"""Hot water use, followed live: when a use starts and ends (a fall in the tank readings, confirmed by a water meter
or a bathroom signal), what it was (shower, bath, tap, coil), gaps in the readings, a use carried on over a restart and
the tank running out while the water ran. One per tank (UseTracker); it owns that state and reaches the rest of
the integration only through UseHost: the readings and the estimate, the ledger it books into, and notify/save/fire.
Pure: no Home Assistant (states, the recorder, its clock) here; the manager feeds the signals in and hands
it the clock, so a use can be replayed without Home Assistant (tests/test_usage_core.py)."""
from __future__ import annotations

from dataclasses import asdict
from datetime import datetime, timedelta
import logging
from typing import Any, Protocol

from . import insights, model
from .clock import Clock, at as _at
from .const import MAX_SHOWER_MINUTES, MIN_SHOWER_MINUTES

_LOGGER = logging.getLogger(__name__)


def _longest(runs: list[tuple[float, float]] | None) -> float | None:
    """Minutes of the longest of the water meter's shower-like flows; None: the meter said nothing."""
    return None if runs is None else max((b - a for a, b in runs), default=0.0)


class UseHost(Protocol):
    """What the use tracker reads from and does through the rest of the integration (the manager)."""
    top: str
    bottom: str | None
    volume: float
    bathtub: bool
    params: dict
    ledger: Any                                     # ledger.ShowerLedger
    estimate: model.Estimate | None                 # the last one: at a new reading still the previous estimate
    top_value: float | None
    bottom_value: float | None
    reheating: bool

    def head_flow(self) -> tuple[float | None, str]: ...
    def loss_k_h(self) -> float: ...
    def tank_now(self) -> tuple[float | None, float | None]: ...
    def notify(self) -> None: ...
    def save_later(self, delay: float) -> None: ...
    def request_save(self) -> None: ...
    def fire(self, kind: str, data: dict) -> None: ...
    def fire_entry(self, kind: str, entry: dict) -> None: ...


class UseTracker:
    """The hot water use going on, and what is kept of past ones (use_stats, run_outs, coil_events)."""

    def __init__(self, host: UseHost, clock: Clock) -> None:
        self.h = host
        self.clock = clock
        self.showering = False                      # a hot water use is going on (shower or not)
        self.use: insights.UseSession | None = None
        # the tank running out while hot water ran: the readings and minutes shown in the hour before, so a
        # diagnostics file shows how far ahead the card saw it coming (#10)
        self.run_outs: list[dict] = []
        self._recent: list[list] = []               # [minute, top, bottom, minutes shown, water L/min], last 90 min
        self.live_offset = 0.0                      # minutes counted down since the last real reading
        self._sensor_at = None                      # utc time of the last top/bottom reading
        self._last_fall_at = None                   # utc time of the last reading that showed hot water use
        self._prev = {}                             # entity -> (value, utc time)
        # gaps in the tank readings (a heat pump's cloud that stops for a while): when its temperatures last
        # changed (minutes), when a tank sensor was unavailable, and when this run started
        self._temp_seen: list[float] = []
        self._unavailable_at: float | None = None
        self._started_at = self._now_min()
        self._gap: dict | None = None               # a gap being closed: the readings before it, the last change
        # what kind of hot water use: followed live, confirmed by heat or a bathroom signal (insights.UseSession)
        self.bath_signal: insights.BathSignal | None = None
        self.water_signal: insights.WaterSignal | None = None   # a water meter, followed live too
        self.use_stats: dict[str, list[float]] = {"shower": [], "tap": [], "shower_rate": []}
        self._hold_until: datetime | None = None    # a restored use is not closed before this while no reading is back
        self._resume: dict | None = None            # readings from before a restart, judged at the first estimate
        self._resume_since = self._now_min()
        self._use_saved: datetime | None = None
        self.coil_events = 0                        # heat that left without water (reheat start, coil)

    def _now_min(self) -> float:
        """Now, in minutes since the epoch."""
        return self.clock.utcnow().timestamp() / 60

    def note_temp(self) -> None:
        """A temperature of the tank's device was reported (it shows the device is alive)."""
        self._temp_seen = (self._temp_seen + [self._now_min()])[-50:]

    def note_unavailable(self) -> None:
        """A tank sensor has no reading now."""
        self._unavailable_at = self._now_min()

    def reading(self) -> None:
        """A new estimate was made from a reading: a restored use is no longer held."""
        self._hold_until = None

    RESUME_MAX = timedelta(minutes=10)       # a use is followed on after a restart this short (twice the quiet period
                                             # that ends one); longer, it is closed from the readings, like a gap
    RESTORE_HOLD = timedelta(minutes=3)      # the tank sensors are often not loaded yet right after a restart
    RESUME_LOOKBACK = timedelta(hours=3)     # a restart longer ago: reheats and more uses in between, nothing to judge

    def restore(self, rec: dict | None) -> None:
        """Home Assistant was restarted: carry on a use that was going on, or judge the heat that left meanwhile
        (the tank readings from before) with the rules for a gap, so it does not turn into "cooling"."""
        at = self.clock.parse(rec["at"]) if isinstance(rec, dict) and rec.get("at") else None
        if at is None:
            return
        if rec.get("top_sensor") != self.h.top or rec.get("bottom_sensor") != self.h.bottom:
            return                                  # other sensors (Reconfigure, options): these readings are not theirs
        now = self.clock.utcnow()
        down = now - at
        if down < timedelta(0) or down > self.RESUME_LOOKBACK:
            return
        top0, bottom0 = rec.get("top"), rec.get("bottom")
        saved = rec.get("use")
        try:
            use = insights.UseSession(**{k: v for k, v in saved["use"].items()
                                         if k in insights.UseSession.__dataclass_fields__}) if saved else None
            start = self.clock.parse(saved["start"]) if saved else None
            fall = self.clock.parse(saved["last_fall"]) if saved else None
        except (KeyError, TypeError, ValueError):
            use = start = fall = None
        if use is not None and start is not None and fall is not None:
            if down <= self.RESUME_MAX:
                self.use, self.h.ledger.session_start, self._last_fall_at, self.showering = use, start, fall, True
                self._hold_until = now + self.RESTORE_HOLD
                # the tank kept falling while Home Assistant was off: the use went on (its end is the restart)
                top, bottom = self.h.tank_now()
                if top is not None and (not self.h.bottom or bottom is not None) and rec.get("avg") is not None \
                        and rec["avg"] - model.tank_average(self.h.params, top, bottom) >= 0.5:
                    self._last_fall_at = now
                return
            # too long to follow on: from the readings before the use to the ones after the restart
            if use.start_top is not None and (use.start_bottom is not None or not self.h.bottom):
                top0, bottom0, at = use.start_top, use.start_bottom, start
        if top0 is not None and (bottom0 is not None or not self.h.bottom):
            self._resume = {"top": top0, "bottom": bottom0, "since": at}

    def apply_resume(self) -> None:
        """The first estimate after a restart: hot water that left since the readings kept before it is a gap."""
        r = self._resume
        if r is None or self.showering or self._gap is not None or self.h.reheating or self.h.estimate is None:
            return
        if self.h.top_value is None or (self.h.bottom and self.h.bottom_value is None):
            return
        self._resume = None
        e, hours = self.h.estimate, (self.clock.utcnow() - r["since"]).total_seconds() / 3600
        if insights.gap_use(self.h.params, (r["top"], r["bottom"]), (self.h.top_value, self.h.bottom_value), e.cold,
                            e.per_minute, self.confirm_k(), cooling=self.h.loss_k_h() * hours) is None:
            return                                  # standing loss only: no use, and no gap to hold the live rules up
        self._gap = {"top": r["top"], "bottom": r["bottom"], "since": r["since"], "last": self.clock.utcnow(),
                     "estimate": self.h.estimate}

    def _drop_restored_use(self) -> None:
        """The sensors did not come back in time to close a restored use: it is left to the gap rules, which judge
        it from the readings before it and the first ones after (nothing is booked when they never return)."""
        use, start = self.use, self.h.ledger.session_start
        self._hold_until = None
        self.showering, self.use, self.h.ledger.session_start = False, None, None
        if use is not None and start is not None and use.start_top is not None \
                and (use.start_bottom is not None or not self.h.bottom):
            self._resume = {"top": use.start_top, "bottom": use.start_bottom, "since": start}
            self._resume_since = self._now_min()
        self.h.notify()

    def running_use(self) -> dict | None:
        if not self.showering or self.use is None or self.h.ledger.session_start is None or self._last_fall_at is None:
            return None
        return {"use": asdict(self.use), "start": self.h.ledger.session_start.isoformat(),
                "last_fall": self._last_fall_at.isoformat()}

    def _reported_before(self, now: datetime) -> datetime | None:
        """When the tank's device last sent a temperature before this reading (one poll writes all its sensors at
        once, so the half minute before now is left out)."""
        cut = now.timestamp() / 60 - 0.5
        seen = [t for t in self._temp_seen if t <= cut]
        return _at(max(seen)) if seen else None

    LIVE_MAX_MINUTES = 4.0          # never count down further than this without a new reading
    USE_TIMEOUT = timedelta(minutes=5)

    def detect(self) -> None:
        """A new tank reading that falls fast (cold water coming in) while not reheating = hot water in use."""
        now = self.clock.utcnow()
        changed, falling, fall_from = False, False, None
        before = {ent: v[0] for ent, v in self._prev.items()}     # readings before this update
        before_at = None                                          # previous reading of the sensor(s) that changed
        confirmed = self._reported_before(now)                    # the device's last report before this one
        for ent, val in ((self.h.top, self.h.top_value), (self.h.bottom, self.h.bottom_value)):
            if not ent or val is None:
                continue
            prev = self._prev.get(ent)
            if prev is None or prev[0] != val:
                changed = True
                if prev is not None:
                    before_at = prev[1] if before_at is None else min(before_at, prev[1])
                    # the value held until the device last reported (most sensors only change state when the
                    # value changes): a fall is as fast as the time since then, not since the value last changed
                    since = max(prev[1], confirmed) if confirmed is not None else prev[1]
                    minutes = max((now - since).total_seconds() / 60, 0.5)
                    # a lower threshold keeps a running use going, and the bathroom signal lowers the start one
                    threshold = (insights.FALL_CONTINUE if self.showering else
                                 insights.FALL_START_BATH if self.signal_active() else insights.FALL_START)
                    if (prev[0] - val) / minutes >= threshold:
                        falling = True
                        fall_from = since if fall_from is None else min(fall_from, since)
                self._prev[ent] = (val, now)
        if not changed:
            return
        self._sensor_at, self.live_offset = now, 0.0       # a real reading: start from the sensors again
        if self.use is not None:
            self.use.readings += 1
        if self._gap is not None:                          # still settling after a gap: one use for all of it
            self._gap["last"] = now
            return
        if before_at is not None and not self.h.reheating and self._gap_start(before, before_at, now):
            return                                         # used while the readings were stale: logged as such
        if falling and not self.h.reheating:
            if not self.showering:
                self.h.ledger.session_start = fall_from or now        # the use began after the previous reading
                # a steady water flow says when it really began (the tank reacts later); the bathroom signal only
                # between the two readings, since a fan or light may be on for other reasons
                water = self.water_signal.rose_at if self.water_signal is not None and self.water_active() else None
                bath = self.bath_signal.rose_at if self.bath_signal is not None and self.bath_active() else None
                if water is not None and now - timedelta(minutes=15) <= _at(water) <= now:
                    self.h.ledger.session_start = _at(water)
                elif bath is not None and fall_from is not None:
                    rose_at = _at(bath)
                    if fall_from <= rose_at <= now:
                        self.h.ledger.session_start = rose_at
                self.use = insights.UseSession(
                    start=self.h.ledger.session_start.timestamp() / 60,
                    start_avg=self.h.estimate.average if self.h.estimate else None,   # still the previous estimate
                    start_top=before.get(self.h.top), start_bottom=before.get(self.h.bottom) if self.h.bottom else None)
                self.h.save_later(5)                 # a restart now must not lose the use
            self.showering, self._last_fall_at = True, now
        elif self.showering and not self._held(now) and (
                self.h.reheating or self._last_fall_at is None or now - self._last_fall_at > self.USE_TIMEOUT):
            self._end_shower()

    def _held(self, now: datetime) -> bool:
        """Right after a restart a restored use is not closed while the tank readings are still missing."""
        return self._hold_until is not None and self.h.estimate is None and now < self._hold_until

    def tick(self, _now) -> None:
        if self.showering and self._hold_until is not None and self.h.estimate is None and self.clock.utcnow() >= self._hold_until:
            self._drop_restored_use()
        if self._resume is not None and self._now_min() - self._resume_since > 30:
            self._resume = None                     # the tank sensors never came back to judge it by
        if self.h.estimate is None:
            return
        now = self.clock.utcnow()
        if self.showering and (self._use_saved is None or now - self._use_saved >= timedelta(seconds=60)):
            self._use_saved = now
            self.h.save_later(5)                     # keeps the running use (and the time) current for a restart
        if self._gap is not None and now - self._gap["last"] >= self.GAP_SETTLE:
            self._gap_use()
        if not self.showering:
            return
        if self.h.reheating or (self._last_fall_at and now - self._last_fall_at > self.USE_TIMEOUT):
            self._end_shower()
            self.live_offset = 0.0
        elif self._sensor_at is not None:
            self.live_offset = min(self.LIVE_MAX_MINUTES, (now - self._sensor_at).total_seconds() / 60)
        self.h.notify()

    def _end_shower(self) -> None:
        """A hot water use ended: log it as a shower or as other hot water use (or ignore it: no water)."""
        self.showering = False
        use, start, end = self.use, self.h.ledger.session_start, self._last_fall_at
        e, ws = self.h.estimate, self.water_signal
        self.use = self.h.ledger.session_start = None
        if use is None or not start or not end:
            return
        if ws is not None and ws.last_flow_at is not None and end < _at(ws.last_flow_at) <= self.clock.utcnow():
            end = _at(ws.last_flow_at)                     # the meter saw the water running until later
        minutes = (end - start).total_seconds() / 60
        kind, rule = use.final(minutes, MAX_SHOWER_MINUTES), None
        # a water meter that was reading during the use is the best witness: a shower flows for minutes without a
        # break, washing up comes in bursts (#10). None: no meter, or it said nothing during the use
        runs = self._meter_runs(use, end.timestamp() / 60)
        longest = _longest(runs)
        metered = longest is not None and longest >= insights.WATER_SHOWER_RUN
        no_shower_flow = longest is not None and not metered
        # with a signal, a short shower must have been seen by it (a humidity sensor can rise after the water stopped)
        signal = (use.bath_seen or use.water_seen or self.signal_active()) if self.bath_signal or ws else None
        if kind == "tap" and not no_shower_flow and insights.short_shower(
                use.drawn, minutes, self._shower_time_of_day(start), signal):
            kind, rule = "shower", "habit"                 # short, but at the hour this household showers
        fresh = self._fresh_bathroom(use)
        if kind == "shower" and not fresh and not metered and e is not None and insights.too_slow_for_a_shower(
                use.drawn, minutes, e.per_minute, use.start_bottom, e.cold, use.readings):
            kind, rule = "tap", "slow"                     # heat enough, but at a fraction of a shower's pace
        if kind == "shower" and no_shower_flow:
            kind, rule = "tap", "water"                    # the meter was reading and never saw a shower's flow
        elif kind == "shower" and not fresh and not self._heat_alone_confirms(longest):
            kind, rule = "tap", "unseen"                   # nor the bathroom, nor a shower's flow long enough (#10)
        tail, drawn = None, use.drawn                      # tail: (start, end, K) of a tap right after the shower
        if kind == "shower" and metered:
            # the shower runs from its first long flow to its last: short bursts after it are a tap, used while the
            # tank still falls (it reports minutes late) and so taken into the same use (#10)
            began, stopped = insights.shower_span(runs, self._water_off)
            after_from, until = stopped + insights.WATER_TAIL_GAP_MINUTES, end.timestamp() / 60
            after, tail_l = ws.water_times(after_from, until), ws.litres_between(after_from, until)
            shower_l = ws.litres_between(began, stopped)
            if after is not None and tail_l >= insights.WATER_TAIL_LITRES and shower_l > 0:
                tail_k = use.drawn * tail_l / (tail_l + shower_l)   # its share of the heat, by the litres
                tail, drawn = (*after, tail_k), use.drawn - tail_k
            # the tank reports late: the minutes the water really ran (a meter total misses the first minute after a
            # quiet spell, so only a clear difference counts)
            ran = sum(max(0.0, min(b, stopped) - max(a, use.start)) for a, b in runs)
            if ran + insights.WATER_GAP_MINUTES < minutes:
                minutes, end = ran, start + timedelta(minutes=ran)
        # why it counts as what it is (for the diagnostics): the heat that left, the signals that saw it, the rule
        bathroom = self.bath_signal is not None and (use.bath_seen or self.bath_active())
        why = {"drawn": round(drawn, 2), "seen": [s for s, on in (("bathroom", bathroom),
                                                                  ("water_meter", use.water_seen or metered)) if on],
               **({"rule": rule} if rule else {})}
        if kind == "shower":
            entry = self.h.ledger.shower_ended(self.h, start, end, e, min_minutes=2.0, extra=why)
            if entry is not None:
                self._remember_use("shower", drawn)
                if minutes >= MIN_SHOWER_MINUTES:          # the pace of a whole shower: tells baths apart
                    self._remember_use("shower_rate", drawn / minutes)
                self.h.fire_entry("shower_ended", entry)
            if tail is not None and tail[2] >= insights.MIN_USE_K:
                self.h.fire_entry("hot_water_used", self.h.ledger.other_ended(
                    self.h, "tap", _at(tail[0]), tail[1] - tail[0], tail[2] * self.h.volume * model.CP,
                    drawn=round(tail[2], 2), seen=["water_meter"], rule="tail"))
        elif (kind == "fill" and self.h.bathtub and e is not None and e.per_minute > 0
              and use.drawn / e.per_minute >= insights.BATH_SHOWER_MINUTES):
            self.h.fire_entry("shower_ended",
                             self.h.ledger.bath_ended(self.h, start, minutes, use.drawn / e.per_minute, e, **why))
        elif kind == "coil":
            self.coil_events += 1
        elif use.drawn >= insights.MIN_USE_K:              # below that nothing measurable left the tank
            if kind == "tap" and rule is None:             # a slow, dry or unseen use says nothing about a tap's heat
                self._remember_use("tap", use.drawn)
            self.h.fire_entry("hot_water_used", self.h.ledger.other_ended(
                self.h, kind, start, minutes, use.drawn * self.h.volume * model.CP, **why))

    def _meter_runs(self, use: insights.UseSession | None, until: float) -> list[tuple[float, float]] | None:
        """The shower-like flows the water meter saw during a use, or None: no meter, or it said nothing since."""
        ws = self.water_signal
        if use is None or ws is None or ws.heard_at is None or ws.heard_at < use.start - insights.WATER_LEAD_MINUTES:
            return None
        return ws.runs_between(use.start - insights.WATER_LEAD_MINUTES, until)

    def _fresh_bathroom(self, use: insights.UseSession | None) -> bool:
        """The bathroom signal rose with this use. Only that says it was a shower: one that rose before is the
        humidity (or fan) of an earlier shower, still on while the dishes are done."""
        sig = self.bath_signal
        return (use is not None and sig is not None and (use.bath_seen or self.bath_active())
                and sig.rose_at is not None and sig.rose_at >= use.start - insights.BATH_LEAD_MINUTES)

    def _heat_alone_confirms(self, longest: float | None) -> bool:
        """May a shower's heat make a use a shower without the bathroom signal? Not with a bathroom signal and a
        water meter that read during the use (``longest``: its longest shower-like flow) but saw no shower's flow
        of UNSEEN_SHOWER_RUN minutes: the kitchen tap then."""
        return self.bath_signal is None or longest is None or longest >= insights.UNSEEN_SHOWER_RUN

    def _water_ran(self) -> bool:
        """The water meter saw the water for the heat the use going on took so far (any flow: a tap goes on and
        off): it is no coil."""
        ws, use, e = self.water_signal, self.use, self.h.estimate
        if ws is None or use is None or e is None:
            return False
        litres = ws.litres_between(use.start - insights.WATER_LEAD_MINUTES, self._now_min())
        return insights.water_explains(litres, use.drawn, self.h.volume, self.h.top_value, e.cold)

    def shower_stopped_at(self) -> datetime | None:
        """With a water meter: when the shower going on stopped running, while the tank still falls (it reports
        minutes late, and a tap may follow). None while the water runs, or without a meter that saw the shower."""
        if not self.showering or self.use is None or self.use.kind != "shower":
            return None
        now = self._now_min()
        runs = self._meter_runs(self.use, now)
        if not runs:
            return None
        span = insights.shower_span(runs, self._water_off)    # rinsing off counts once it ran a little: not a tap
        if span is None or now - span[1] < insights.WATER_GAP_MINUTES:
            return None
        return _at(span[1])

    def _water_off(self, t0: float, t1: float) -> bool:
        """The water meter saw no water between t0 and t1 (minutes)."""
        ws = self.water_signal
        return ws is not None and ws.litres_between(t0, t1) < insights.WATER_TAIL_LITRES

    GAP_SETTLE = timedelta(minutes=1)    # after a gap, the tank sensors come back one by one: wait for all

    def _gap_start(self, before: dict, before_at: datetime, now: datetime) -> bool:
        """A tank reading after a gap (the device sent nothing for a while, a sensor was unavailable, or the last
        reading was stale): hot water used meanwhile is not followed live but logged from the heat it took, once
        the readings have settled. Returns True when the reading starts such a gap."""
        e, top0 = self.h.estimate, before.get(self.h.top)
        bottom0 = before.get(self.h.bottom) if self.h.bottom else None
        if e is None or top0 is None or self.h.top_value is None or (self.h.bottom and (bottom0 is None or self.h.bottom_value is None)):
            return False
        drop = model.tank_average(self.h.params, top0, bottom0) - model.tank_average(self.h.params, self.h.top_value, self.h.bottom_value)
        if not insights.pace_unknown(now.timestamp() / 60, before_at.timestamp() / 60, self._temp_seen, self._started_at,
                                     self._unavailable_at, drop, self.fill_rate(), e.per_minute):
            return False
        if self.showering:
            self._end_shower()                       # a use that was going on when the readings stopped
        self._gap = {"top": top0, "bottom": bottom0, "since": before_at, "last": now, "estimate": e}
        return True

    def _gap_use(self) -> None:
        """Log the hot water used during a gap, from the readings before it to the settled ones after it."""
        g, self._gap = self._gap, None
        e, now = g["estimate"] or self.h.estimate, self.clock.utcnow()
        if e is None:
            return
        if self.h.top_value is None or (self.h.bottom and self.h.bottom_value is None):
            return
        hours = (g["last"] - g["since"]).total_seconds() / 3600
        res = insights.gap_use(self.h.params, (g["top"], g["bottom"]), (self.h.top_value, self.h.bottom_value), e.cold,
                               e.per_minute, self.confirm_k(), cooling=self.h.loss_k_h() * hours)
        if res is None:
            return
        kind, drop_k, minutes = res
        if kind == "coil":
            self.coil_events += 1
            return
        before_at, t_prev, t_now = g["since"], g["since"].timestamp() / 60, g["last"].timestamp() / 60
        sig, bath_at = self.bath_signal, None        # when the bathroom was in use during the gap: the start
        if sig is not None:
            for t in (sig.rose_at, sig.active_at):
                if t is not None and t_prev <= t <= t_now:
                    bath_at = t if bath_at is None else min(bath_at, t)
        span = (g["last"] - before_at).total_seconds() / 60
        if bath_at is not None:
            start = _at(bath_at)
        else:                                        # somewhere in the gap: its middle
            start = before_at + timedelta(minutes=max(0.0, (span - minutes) / 2))
        if kind == "shower":
            usual = [x["minutes"] for x in self.h.ledger.log
                     if x.get("source") == "live" and x.get("kind", "shower") == "shower" and not x.get("estimated")]
            minutes = insights.gap_minutes(minutes, usual)
            entry = self.h.ledger.estimated_ended(self.h, start, minutes, e)
            self.h.fire_entry("shower_ended", entry)
        else:
            entry = self.h.ledger.other_ended(self.h, kind, start, 0.0, drop_k * self.h.volume * model.CP, estimated=True)
            self.h.fire_entry("hot_water_used", entry)
        _LOGGER.debug("Hot water used during a gap in the tank readings (%.0f min): %s", span, entry)
        self.h.notify()

    def bath_active(self) -> bool:
        return bool(self.bath_signal and self.bath_signal.active(self._now_min()))

    def water_active(self) -> bool:
        return bool(self.water_signal and self.water_signal.active(self._now_min()) and not self.fast_flow())

    def fast_flow(self) -> bool:
        """The water meter shows far more than the shower head gives (a bath tap): not a shower."""
        if self.water_signal is None or not self.water_signal.flowing(self._now_min()):
            return False
        head = self.h.head_flow()[0]
        return bool(head) and self.water_signal.flow >= insights.FAST_FLOW_FACTOR * head

    def signal_active(self) -> bool:
        """Something besides the tank says a shower is going on: the bathroom, or a steady flow of water."""
        return self.bath_active() or self.water_active()

    def confirm_k(self) -> float:
        return insights.learn_confirm_k(self.use_stats["shower"], self.use_stats["tap"])

    def fill_rate(self) -> float:
        """Tank average fall (K/min) from which hot water is a bath or bucket, from this household's showers."""
        return insights.fill_rate(self.h.estimate.per_minute if self.h.estimate else None, self.use_stats.get("shower_rate"))

    def _shower_time_of_day(self, at: datetime) -> bool:
        """Is this the time of day this household usually showers (from the shower log and the other hot water)?"""
        def tod(iso: str) -> float:
            t = self.clock.as_local(datetime.fromisoformat(iso))
            return t.hour * 60 + t.minute
        local = self.clock.as_local(at)
        log = self.h.ledger.showers()                 # one showers after the other, not after a bath (#10)
        taps = [tod(e["start"]) for e in self.h.ledger.other if e.get("kind") == "tap"]
        since_last = (at - datetime.fromisoformat(log[-1]["start"])).total_seconds() / 60 if log else None
        return insights.shower_time_of_day(local.hour * 60 + local.minute, [tod(e["start"]) for e in log], taps,
                                           since_last)

    def _remember_use(self, kind: str, drawn: float) -> None:
        self.use_stats[kind] = (self.use_stats[kind] + [round(drawn, 2)])[-50:]
        self.h.request_save()

    def track_run_out(self) -> None:
        """Note when the tank runs out while hot water runs: the top falls below the shower temperature + 2 (where
        the minutes reach 0) from a hot tank. Also while reheating: a long draw (a bath) starts the heat pump just
        as the tank runs out, so that is when most run-outs happen (#10); the water running and a top that was hot
        this hour keep a reheat on its own from counting. Keeps the last hour of readings and the minutes the card
        showed, so every run-out is a check of the estimate (``run_outs``, last 10, in diagnostics)."""
        e, top, now = self.h.estimate, self.h.top_value, self._now_min()
        if e is None or top is None:
            return
        w = self.water_signal
        flow = None
        if w is not None:   # a meter that counts litres keeps its last flow until it moves again: stale once still
            fresh = w.kind == "rate" or (w.last_flow_at is not None and now - w.last_flow_at <= insights.WATER_GAP_MINUTES)
            flow = round(w.flow, 2) if fresh else 0.0
        row = [now, round(top, 2), None if self.h.bottom_value is None else round(self.h.bottom_value, 2),
               round(e.minutes, 1), flow]
        if self._recent and self._recent[-1][0] == now:
            self._recent[-1] = row
        else:
            self._recent.append(row)
        self._recent = [r for r in self._recent if r[0] >= now - 90]
        edge = e.shower_temp + 2                    # where the card reaches 0 (in auto mode the sensed temperature)
        last = self.run_outs[-1]["minute"] if self.run_outs else None
        if (top >= edge or not ((self.showering and self.use_kind() != "coil") or (flow or 0.0) >= 1.0)
                or (last is not None and now - last < 180)):
            return
        hour = [r for r in self._recent if r[0] >= now - 60]
        if len(hour) < 2 or hour[-2][1] < edge or max(r[1] for r in hour) < edge + 3:
            return                                  # it just crossed now, from a tank that was hot this hour
        self.run_outs = (self.run_outs + [{
            "minute": now, "at": self.clock.now().isoformat(timespec="minutes"), "shower_temp": round(e.shower_temp, 2),
            "kind": self.use_kind(), "top": row[1], "bottom": row[2], "reheating": self.h.reheating,
            "trace": [[r[0] - now] + r[1:] for r in hour]}])[-10:]
        self.h.request_save()

    def use_kind(self) -> str | None:
        """What the hot water use going on now looks like: unknown (just started, or a tap), shower, fill or
        coil (heat leaving without water)."""
        return self.use.kind if self.showering and self.use else None

    def classify(self) -> None:
        if not self.showering or self.use is None or self.h.estimate is None:
            return
        if self.water_active() and self.water_signal.rose_at is not None and self.h.ledger.session_start is not None:
            began = _at(self.water_signal.rose_at)
            if began < self.h.ledger.session_start and self.clock.utcnow() - began <= timedelta(minutes=15):
                self.h.ledger.session_start = began           # the water was running before the tank showed it
                self.use.start = self.water_signal.rose_at
        # with a bathroom signal only that one lowers the threshold: a kitchen tap can run as steadily as a shower
        lowers = self.bath_active() if self.bath_signal is not None else self.water_active()
        now = self._now_min()
        kind_before = self.use.kind
        if self.use.update(now, self.h.estimate.average, lowers,
                           self.confirm_k(), top=self.h.top_value, bottom=self.h.bottom_value, cold=self.h.estimate.cold,
                           fill_rate=self.fill_rate(), fast_flow=self.fast_flow(), water_active=self.water_active(),
                           water_ran=self._water_ran(),
                           heat_confirms=(self._heat_alone_confirms(_longest(self._meter_runs(self.use, now)))
                                          or self._fresh_bathroom(self.use))):
            self.h.fire("shower_started", {"started": self.clock.as_local(self.h.ledger.session_start).isoformat(timespec="seconds"),
                                          "confirmed_after_minutes": self.use.confirmed_after,
                                          "bathroom_signal": self.use.bath_seen,
                                          "water_meter": self.water_active()})
        if self.use is not None and self.use.kind != kind_before:
            self.h.save_later(5)
