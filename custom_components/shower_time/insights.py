"""Smart extras on top of the tank model: shower habits, today's forecast and heat-loss (leak) watching.

Pure Python (no Home Assistant imports) so it can be unit tested on its own, like model.py.

Shower habits
    Every completed day adds the minutes showered per hour to a running profile, per weekday and for all
    days together. The weekday profile counts for more as more of those weekdays have been seen.

Today's forecast
    The minutes still expected today (from the habits) against what the tank holds now, minus the standing
    loss until then. Without a reheat in between: when would the hot water run out?

What kind of hot water use
    The tank only sees heat leaving it, so a shower and a sink being filled look the same at first. They differ
    in how much heat they take and how fast: washing up takes a few tenths of a degree from the tank average,
    a shower several degrees at a steady pace, a bath or bucket filled with hot water only falls about three
    times faster than a shower. A use is shown as hot water first and becomes a shower once enough heat has
    left at a shower's pace, or right away when a bathroom signal (humidity rising, the bathroom fan running
    harder, a fan or light switched on) confirms it. The threshold is learned per household.
    Water drawn from the tank is replaced by cold water at the bottom, so the top stays hot until the tank is
    nearly empty. When the top falls while the bottom is still warm, or falls more than the bottom, heat leaves
    without water: a heat pump starting a reheat (the coil is cold for a moment) or heat taken out through the
    coil. That is not counted as hot water use.

Heat loss (insulation and leaks)
    Each day the longest quiet stretch (no reheating, no hot water use, layers settled) while the tank is
    evenly hot gives a clean measurement of how fast it cools. Divided by how much warmer the tank is than the
    room, that is the tank's heat-loss coefficient (W/K): it does not depend on how hot the tank happens to be,
    so it can be compared over months. Against the household's own baseline it shows worsening insulation; and
    a dripping hot tap shows as the bottom cooling much faster than the top (cold water trickling in), while in
    a healthy tank they cool together.
"""
from __future__ import annotations

import statistics
from dataclasses import asdict, dataclass
from datetime import date

try:
    from . import model
except ImportError:          # imported on its own by the tests
    import model

HOURS = 24
HABIT_READY_DAYS = 7          # forecast only after a week of observed days
WEEKDAY_WEIGHT = 3.0          # pseudo-days: how fast the weekday profile takes over from the all-days profile

ROOM_TEMP = 20.0              # assumed temperature around the tank (°C)
EVEN_SPREAD = 3.0             # top and bottom within this many K = evenly hot tank
QUIET_MIN_HOURS = 3.0
TAP_DROP = 0.3                # K fall of the bottom within TAP_MINUTES = someone used hot water
TAP_MINUTES = 3               # a tap drops the bottom quickly; a drip lowers it slowly and steadily
SETTLE_MINUTES = 60           # after a reheat or hot water use the layers keep moving for a while
MIN_ABOVE_ROOM = 15.0         # the tank must be at least this much warmer than the room to measure
LOSS_BASELINE_DAYS = 7        # measurements needed before the trend is judged
LOSS_RECENT = 4               # the last measurements compared with the baseline
LOSS_KEEP = 120
LOSS_RATIO = 1.4              # coefficient this much above the baseline, in 3 of the last 4 = raised heat loss
LEAK_BOTTOM_SHARE = 2.0       # bottom/top cooling ratio this many times the usual one = leak-like
DRIP_START, DRIP_END = 1, 5   # night hours checked for a steady drip
DRIP_EXCESS = 0.4             # K the bottom falls more than the top within one such hour
DRIP_NIGHTS = 3               # nights out of the last 4 with a steady drip in most hours = leak-like

# --------------------------------------------------------------------------- shower habits

def new_habits() -> dict:
    return {"days": 0, "all": [0.0] * HOURS, "weekday": {str(d): {"days": 0, "hours": [0.0] * HOURS} for d in range(7)},
            "last_day": None}


def minutes_by_hour(showers: list[tuple[float, float]]) -> list[float]:
    """[(start hour as a float, e.g. 21.5, minutes), ...] for one day -> minutes per hour, spread over the
    hours a shower actually covers (a shower from 21:50 to 22:05 counts in both)."""
    out = [0.0] * HOURS
    for start, minutes in showers:
        t, left = start, minutes
        while left > 1e-9 and t < HOURS:
            h = int(t)
            take = min(left, (h + 1 - t) * 60)
            out[h] += take
            left -= take
            t = h + 1
    return out


def add_day(habits: dict, weekday: int, day: str, per_hour: list[float]) -> dict:
    """Add one completed day. Running mean for the first weeks, then an average over roughly the last
    6 weeks per weekday and 3 weeks for all days, so changed habits (school holidays) come through."""
    h = habits
    wd = h["weekday"][str(weekday)]
    a_all = 1 / min(h["days"] + 1, 21)
    a_wd = 1 / min(wd["days"] + 1, 6)
    h["all"] = [round(o + a_all * (x - o), 3) for o, x in zip(h["all"], per_hour)]
    wd["hours"] = [round(o + a_wd * (x - o), 3) for o, x in zip(wd["hours"], per_hour)]
    h["days"] += 1
    wd["days"] += 1
    h["last_day"] = day
    return h


def mark_incomplete(habits: dict, days: list[str]) -> None:
    """Days on which showers could have been missed (Home Assistant was off, the tank sensors were silent):
    they are left out of the habits instead of counting as days without showers."""
    skip = habits.setdefault("skip", [])
    for d in days:
        if d not in skip:
            skip.append(d)
    habits["skip"] = sorted(skip)[-60:]


def is_incomplete(habits: dict, day: str) -> bool:
    return day in habits.get("skip", [])


def expected_by_hour(habits: dict, weekday: int) -> list[float]:
    """Expected shower minutes per hour for a weekday: its own profile, shrunk towards all days."""
    wd = habits["weekday"][str(weekday)]
    k = wd["days"] / (wd["days"] + WEEKDAY_WEIGHT)
    return [k * w + (1 - k) * a for w, a in zip(wd["hours"], habits["all"])]


def habits_ready(habits: dict) -> bool:
    return habits["days"] >= HABIT_READY_DAYS


# --------------------------------------------------------------------------- today's forecast

@dataclass
class Forecast:
    expected_minutes: float          # shower minutes still expected today
    available_minutes: float         # what the tank holds by the end of the day without a reheat
    enough: bool                     # enough as it is, or a reheat before the shortage is likely
    runs_out_at: float | None        # hour of the day (e.g. 21.4) the hot water would run out without a reheat
    next_shower_at: float | None     # hours from now until the next likely shower (today or tomorrow)
    reheat_chance: float | None = None   # chance of a reheat before it runs out (None: not learned yet)

    def as_dict(self) -> dict:
        return asdict(self)


def forecast(habits: dict, *, weekday: int, hour: float, minutes_now: float, per_minute: float, loss_k_h: float,
             typical: float) -> Forecast:
    """``hour`` is the current local time as a float (21.5 = 21:30); ``per_minute`` the tank average drop per
    shower minute (K/min) and ``loss_k_h`` the standing loss (K/h), both from the model.

    Most heat pumps and boilers reheat on their own (on a schedule, or when the tank runs low). When this
    household's tank is usually reheated in the hours before the shortage, it is not one: ``enough`` stays True
    and ``reheat_chance`` says how likely that reheat is."""
    today = expected_by_hour(habits, weekday)
    tomorrow = expected_by_hour(habits, (weekday + 1) % 7)
    lost_per_hour = loss_k_h / per_minute if per_minute > 0 else 0.0     # shower minutes lost per hour of waiting
    left, need, runs_out = minutes_now, 0.0, None
    for h in range(int(hour), HOURS):
        begin = hour if h == int(hour) else float(h)
        frac = h + 1 - begin
        rate = lost_per_hour + today[h]          # minutes per hour leaving the tank in this hour
        need += today[h] * frac
        if runs_out is None and today[h] > 0 and left - rate * frac < 0:
            runs_out = begin + max(0.0, left) / rate       # the tank no longer covers the expected use
        left -= rate * frac
    available = minutes_now - lost_per_hour * (HOURS - hour)
    nxt = None
    threshold = max(1.0, 0.3 * typical)
    for i, minutes in enumerate([today[h] for h in range(int(hour) + 1, HOURS)] + tomorrow):
        if minutes >= threshold:
            nxt = round(int(hour) + 1 + i - hour, 2)
            break
    chance = reheat_chance(habits, hour, runs_out) if runs_out is not None else None
    enough = runs_out is None or (chance is not None and chance >= REHEAT_LIKELY)
    return Forecast(expected_minutes=round(need, 1), available_minutes=round(max(0.0, available), 1),
                    enough=enough, runs_out_at=round(runs_out, 2) if runs_out is not None else None,
                    next_shower_at=nxt, reheat_chance=round(chance, 2) if chance is not None else None)


# --------------------------------------------------------------------------- reheat habits

REHEAT_READY_DAYS = 7
REHEAT_LIKELY = 0.6           # a reheat this likely before the shortage: no shortage


def add_reheat_day(habits: dict, started: list[int]) -> None:
    """One completed day: the hours (0-23) in which a reheat started. Per hour the share of days with one,
    averaged over roughly the last three weeks."""
    r = habits.setdefault("reheat", {"days": 0, "hours": [0.0] * HOURS})
    a = 1 / min(r["days"] + 1, 21)
    hit = set(started)
    r["hours"] = [round(o + a * ((1.0 if h in hit else 0.0) - o), 3) for h, o in enumerate(r["hours"])]
    r["days"] += 1


def reheat_chance(habits: dict, hour: float, until: float) -> float | None:
    """Chance that the tank is reheated between now (hour of the day) and ``until``, or None while there are
    too few days to tell."""
    r = habits.get("reheat")
    if not r or r["days"] < REHEAT_READY_DAYS:
        return None
    none = 1.0
    for h in range(int(hour), min(HOURS, int(until) + 1)):
        begin = max(hour, float(h))
        end = min(until, h + 1.0)
        if end > begin:
            none *= 1 - min(1.0, r["hours"][h]) * (end - begin)
    return 1 - none


def reheat_starts(rows: list[model.Row], *, local_of) -> dict[str, list[int]]:
    """Per local day the hours in which a reheat started (reheats within an hour of each other count once)."""
    out: dict[str, list[int]] = {}
    prev, last = False, None
    for r in rows:
        if r.heating and not prev and (last is None or r.minute - last > 60):
            t = local_of(r.minute)
            out.setdefault(t.date().isoformat(), []).append(t.hour)
            last = r.minute
        prev = r.heating
    return out


# --------------------------------------------------------------------------- what kind of hot water use

SHOWER_CONFIRM_K = 1.8        # tank average drop that makes a use a shower (until learned per household)
BATH_CONFIRM_K = 0.6          # with a bathroom signal a much smaller drop is enough
FILL_RATE = 1.2               # K/min, far faster than any shower: a bath or bucket filled with hot water only
FILL_MIN_K = 2.0
FILL_FACTOR = 2.0             # ... or twice the pace of this household's showers, once measured (a bath tap runs
                              # about twice a shower head's flow)
FILL_FACTOR_MODEL = 2.5       # ... or this many times the model's shower minute until then: the tank average falls
                              # faster than the model's minute early in a shower (up to 2.4 times measured)
FILL_RECLASSIFY_K = 4.0       # a use already taken for a shower becomes a fill only once this much heat left: the
                              # first minutes of a shower can fall fast
COIL_TOP_DROP = 1.0           # K the top falls while the bottom is still warm (or falls less) = no water drawn
COIL_BOTTOM_ABOVE_COLD = 15.0
BATH_SIGNAL_MINUTES = 10      # a bathroom signal this long before or during the use counts
HUMIDITY_RISE = 3.0           # %RH above the lowest value of the last 15 minutes
POWER_RISE = 1.0              # W above the usual (median) power of the last day, and at least 30 % more
FALL_START = 0.3              # K/min the tank must fall between two readings to start a use ...
FALL_START_BATH = 0.15        # ... or this much when the bathroom signal is on (a full, hot tank responds slowly)
FALL_CONTINUE = 0.1           # K/min that keeps a running use going (the end of a shower falls more slowly)


def fill_rate(per_minute: float | None, shower_rates: list[float] | None = None) -> float:
    """Tank average fall (K/min) from which hot water is a bath or bucket being filled, not a shower: twice the
    pace this household's showers were measured at (five or more), else a margin over the model's minute."""
    model_rate = FILL_FACTOR_MODEL * per_minute if per_minute and per_minute > 0 else FILL_RATE
    if shower_rates and len(shower_rates) >= 5:
        return max(FILL_FACTOR * statistics.median(shower_rates), FILL_FACTOR * (per_minute or 0.0))
    return model_rate


@dataclass
class UseSession:
    """One hot water use, followed live. Times are minutes on any clock."""
    start: float
    start_avg: float | None              # tank average just before the use
    start_top: float | None = None
    start_bottom: float | None = None
    kind: str = "unknown"                # unknown -> shower, fill or coil; a use never confirmed was a tap
    drawn: float = 0.0                   # K the tank average fell since the start
    confirmed_after: float | None = None
    bath_seen: bool = False

    def update(self, t: float, avg: float | None, bath_active: bool, confirm_k: float = SHOWER_CONFIRM_K, *,
               top: float | None = None, bottom: float | None = None, cold: float | None = None,
               fill_rate: float = FILL_RATE, fast_flow: bool = False) -> bool:
        """Feed a new reading; returns True at the moment the use is recognised as a shower."""
        if avg is not None and self.start_avg is not None:
            self.drawn = max(self.drawn, self.start_avg - avg)
        self.bath_seen = self.bath_seen or bool(bath_active)
        if self.kind not in ("unknown", "shower"):
            return False
        if top is not None and bottom is not None and self.start_top is not None and self.start_bottom is not None:
            top_drop, bottom_drop = self.start_top - top, self.start_bottom - bottom
            warm_bottom = cold is not None and bottom > cold + COIL_BOTTOM_ABOVE_COLD
            if top_drop >= COIL_TOP_DROP and (top_drop > bottom_drop or warm_bottom):
                self.kind = "coil"               # heat left without water: not a use after all
                return False
        rate = self.drawn / max(1.0, t - self.start)
        if self.drawn >= (FILL_MIN_K if self.kind == "unknown" else FILL_RECLASSIFY_K) and rate >= fill_rate:
            self.kind = "fill"                   # also when it looked like a shower at first: a bath runs faster
            return False
        if self.kind != "unknown":
            return False
        if fast_flow:                            # the water meter runs far faster than the shower head: a bath tap
            if self.drawn >= BATH_CONFIRM_K:
                self.kind = "fill"
            return False
        if self.drawn >= (BATH_CONFIRM_K if self.bath_seen else confirm_k):
            self.kind = "shower"
            self.confirmed_after = round(t - self.start, 1)
            return True
        return False

    def final(self, minutes: float, max_shower: float, min_shower: float = 2.0) -> str:
        """shower, fill (bath or bucket), tap (washing up, hand washing), coil (no water drawn) or other (a use
        too long for a shower). A shower confirmed by its heat counts from ``min_shower`` minutes."""
        if self.kind == "shower":
            return "shower" if min_shower <= minutes <= max_shower else "other"
        return self.kind if self.kind in ("fill", "coil") else "tap"


# --------------------------------------------------------------------------- uses hidden by a data gap

GAP_MINUTES = 15.0            # the tank's device sent no temperature for this long: when hot water went is unknown
                              # (a Weheat is silent for up to ~20 minutes at night; outages last far longer)
GAP_DROP_K = 1.5              # ... and the tank average fell at least this much in the meantime
GAP_PACE_FACTOR = 3.0         # a fall this many times faster than a bath fills: the earlier reading was stale
BATH_SHOWER_MINUTES = 6.0     # a fill that took the heat of this many shower minutes is a bath (a session, like a
                              # shower); less is a bucket or a sink


def pace_unknown(now: float, prev_reading: float, temp_seen: list[float], started: float,
                 unavailable_at: float | None, drop: float, fill_rate: float = FILL_RATE,
                 per_minute: float | None = None) -> bool:
    """Is the pace of a fall between two tank readings unknown? Times are minutes.

    True when a tank sensor was unavailable since the previous reading, when the fall is far faster than even a
    bath could cause (the previous reading was stale), or when the tank's device sent no temperature for
    GAP_MINUTES before this reading and the tank fell more meanwhile than one reading into a shower shows (three
    shower minutes; ``temp_seen``: when its temperature sensors last changed — a tank sensor that does not change
    says nothing, the others change every few minutes).
    Changes within half a minute of this reading are left out: one poll writes all sensors at once."""
    if drop < 0.15:
        return False
    if unavailable_at is not None and unavailable_at >= prev_reading:
        return True
    if drop / max(0.5, now - prev_reading) > GAP_PACE_FACTOR * fill_rate:
        return True
    seen = [t for t in temp_seen if t <= now - 0.5]
    last = max(seen) if seen else started
    return now - last > GAP_MINUTES and drop >= max(GAP_DROP_K, 3 * (per_minute or 0.0))


def gap_use(params: dict, before: tuple[float, float | None], after: tuple[float, float | None], cold: float,
            per_minute: float, confirm_k: float = SHOWER_CONFIRM_K, cooling: float = 0.0) -> tuple[str, float, float] | None:
    """Hot water used while the readings were stale: (kind, tank average fall in K, shower-minute equivalent), or
    None when nothing measurable left the tank. The pace is unknown, so a shower and a bath look the same: enough
    heat for a shower makes it a session, less is a tap (a bathroom signal only tells when it began: a fan or light
    can be on for other reasons). "coil" when the heat left without water (the top fell, the bottom stayed warm).
    ``cooling`` (K) is what the tank lost by itself during the gap."""
    a0 = model.tank_average(params, before[0], before[1])
    a1 = model.tank_average(params, after[0], after[1])
    drop = a0 - a1 - max(0.0, cooling)
    if drop < 0.15:
        return None
    if before[1] is not None and after[1] is not None:
        top_drop, bottom_drop = before[0] - after[0], before[1] - after[1]
        if top_drop >= COIL_TOP_DROP and (top_drop > bottom_drop or after[1] > cold + COIL_BOTTOM_ABOVE_COLD):
            return "coil", round(drop, 2), 0.0
    minutes = drop / per_minute if per_minute > 0 else 0.0
    kind = "shower" if drop >= confirm_k else "tap"
    return kind, round(drop, 2), round(minutes, 1)


def _quantile(values: list[float], q: float) -> float:
    v = sorted(values)
    pos = q * (len(v) - 1)
    lo = int(pos)
    return v[lo] + (v[min(lo + 1, len(v) - 1)] - v[lo]) * (pos - lo)


def learn_confirm_k(showers: list[float], taps: list[float]) -> float:
    """Threshold between this household's taps and showers (tank average drop, K): the geometric middle of the
    largest taps and the smallest showers. The default until both have been seen five times, or when they
    overlap."""
    if len(showers) < 5 or len(taps) < 5:
        return SHOWER_CONFIRM_K
    s10, t90 = _quantile(showers, 0.1), _quantile(taps, 0.9)
    if s10 <= t90:
        return SHOWER_CONFIRM_K
    return round(min(2.5, max(1.0, (s10 * max(t90, 0.01)) ** 0.5)), 2)


class BathSignal:
    """Is the bathroom in use? From a humidity sensor (rising), a power sensor such as a bathroom fan (above its
    usual power, learned) or anything that switches on (fan, light, motion). Times are minutes."""

    def __init__(self, kind: str) -> None:
        self.kind = kind                         # humidity, power or on_off
        self.values: list[tuple[float, float]] = []
        self.active_at: float | None = None
        self.rose_at: float | None = None        # when it last became active: the likely start of a shower

    def feed(self, t: float, value) -> None:
        if value is None:
            return
        if self.kind == "on_off":
            if value:
                self._on(t)
            return
        v = float(value)
        if not self.values or self.values[-1][1] != v:
            self.values.append((t, v))
        keep = [x for x in self.values if x[0] >= t - 1440]
        older = [x for x in self.values if x[0] < t - 1440]
        self.values = older[-1:] + keep          # plus the value in effect a day ago
        if self.kind == "humidity":
            # the values of the last 15 minutes, including the one that was in effect when they began
            older = [x[1] for x in self.values if x[0] < t - 15]
            window = [x[1] for x in self.values if x[0] >= t - 15] + older[-1:]
            if v - min(window) >= HUMIDITY_RISE:
                self._on(t)
        else:
            if len(self.values) >= 10:
                usual = statistics.median(x[1] for x in self.values)
                if v >= usual + max(POWER_RISE, 0.3 * usual):
                    self._on(t)

    def _on(self, t: float) -> None:
        if not self.active(t):
            self.rose_at = t
        self.active_at = t

    def active(self, t: float) -> bool:
        return self.active_at is not None and t - self.active_at <= BATH_SIGNAL_MINUTES


WATER_SHOWER_FLOW = (4.0, 16.0)   # L/min a shower draws from the mains (hot and cold together)
WATER_STEADY_MINUTES = 2.0        # that long without a break = a shower, not a sink being filled
WATER_GAP_MINUTES = 2.0           # a meter total that has not moved for this long: the water is off
FAST_FLOW_FACTOR = 1.6            # this many times the shower head's flow: a bath tap, not a shower


class WaterSignal:
    """Live water flow from a water meter: a total in litres or a flow in L/min (converted by the caller).
    Times are minutes. It knows when a steady, shower-like flow began and when water last flowed."""

    def __init__(self, kind: str) -> None:
        self.kind = kind                          # "total" or "rate"
        self.last: tuple[float, float] | None = None
        self.flow_since: float | None = None      # start of the current steady flow
        self.last_flow_at: float | None = None    # last moment a shower-like flow was seen
        self.flow = 0.0

    def feed(self, t: float, value) -> None:
        if value is None:
            return
        v = float(value)
        if self.kind == "total":
            prev, self.last = self.last, (t, v)
            if prev is None or t <= prev[0] or v < prev[1]:
                return                            # first reading, or the meter was reset
            if v == prev[1]:
                return
            began = prev[0] if self.last_flow_at is None or prev[0] - self.last_flow_at > WATER_GAP_MINUTES else None
            self._flow(t, (v - prev[1]) / (t - prev[0]), began)
        else:
            self._flow(t, v, None)

    def _flow(self, t: float, flow: float, began: float | None) -> None:
        lo, hi = WATER_SHOWER_FLOW
        was, self.flow = self.flow, flow
        shower_like = lo <= flow <= hi
        if self.kind == "rate":
            # a flow sensor only reports changes: its value holds until the next one
            if shower_like:
                if self.flow_since is None:
                    self.flow_since = t
                self.last_flow_at = t
            else:
                if lo <= was <= hi:
                    self.last_flow_at = t            # the water ran until this moment
                self.flow_since = None
            return
        # a meter total moves while water runs; a pause of WATER_GAP_MINUTES means the water was off
        if shower_like:
            if self.flow_since is None or (self.last_flow_at is not None and t - self.last_flow_at > WATER_GAP_MINUTES):
                self.flow_since = began if began is not None else t
            self.last_flow_at = t
        else:
            self.flow_since = None                   # a trickle or a garden hose: not a shower

    def flowing(self, t: float) -> bool:
        if self.kind == "rate":
            return WATER_SHOWER_FLOW[0] <= self.flow <= WATER_SHOWER_FLOW[1]
        return self.last_flow_at is not None and t - self.last_flow_at <= WATER_GAP_MINUTES

    def active(self, t: float) -> bool:
        """A steady, shower-like flow is going on (or just stopped)."""
        return self.flowing(t) and self.flow_since is not None and t - self.flow_since >= WATER_STEADY_MINUTES

    @property
    def rose_at(self) -> float | None:
        return self.flow_since


def draw_heat(params: dict, rows: list[model.Row], start: int, minutes: int) -> float:
    """Tank average drop (K) of a draw found in the history: start against the lowest point in the 10 minutes
    after it, as the layers settle."""
    seg = []
    for r in rows:
        if r.minute < start:
            continue
        if r.minute > start + minutes + 10 or (r.heating and seg):
            break
        seg.append(r)
    if not seg:
        return 0.0
    a0 = model.tank_average(params, seg[0].top, seg[0].bottom)
    return max(0.0, a0 - min(model.tank_average(params, r.top, r.bottom) for r in seg))


def draw_kind(params: dict, rows: list[model.Row], start: int, minutes: int, cold: float,
              confirm_k: float = SHOWER_CONFIRM_K, fill_rate: float = FILL_RATE) -> str:
    """The same judgement for a draw found in the history: shower, tap, fill (a bath or bucket) or coil (heat
    left without water).
    Like the live judgement it stops where a reheat begins (a heat pump starting up dips the top)."""
    seg = []
    for r in rows:
        if r.minute < start:
            continue
        if r.minute > start + minutes + 10 or r.heating:
            break
        seg.append(r)
    if not seg:
        return "tap"
    first = seg[0]
    if first.bottom is not None:
        bottom_drop = first.bottom - min(r.bottom for r in seg if r.bottom is not None)
        top_drop = first.top - min(r.top for r in seg)
        dipped = next((r for r in seg if first.top - r.top >= COIL_TOP_DROP), None)
        if dipped is not None and (top_drop > bottom_drop or (dipped.bottom is not None
                                                              and dipped.bottom > cold + COIL_BOTTOM_ABOVE_COLD)):
            return "coil"
    heat = draw_heat(params, rows, start, minutes)
    if heat >= FILL_MIN_K and heat / max(1, minutes) >= fill_rate:
        return "fill"
    return "shower" if heat >= confirm_k else "tap"


# --------------------------------------------------------------------------- heat loss

@dataclass
class LossSample:
    day: str                   # local date the quiet stretch ended on
    hours: float
    average: float             # mean tank temperature during the stretch (°C)
    rate: float                # K/h the tank cooled
    top_rate: float            # K/h
    bottom_rate: float | None  # K/h
    ua: float                  # heat-loss coefficient, W/K

    def as_dict(self) -> dict:
        return asdict(self)


def _slope(points: list[tuple[float, float]]) -> float:
    """Least-squares slope (per hour) of [(hours, value), ...]."""
    n = len(points)
    mx = sum(p[0] for p in points) / n
    my = sum(p[1] for p in points) / n
    den = sum((p[0] - mx) ** 2 for p in points)
    return sum((p[0] - mx) * (p[1] - my) for p in points) / den if den else 0.0


def quiet_stretches(rows: list[model.Row]) -> list[list[model.Row]]:
    """Stretches of at least QUIET_MIN_HOURS without reheating or hot water use (the bottom falling TAP_DROP
    within TAP_MINUTES), starting SETTLE_MINUTES after either of those, and only while the tank is evenly hot
    (with a bottom sensor)."""
    out, seg, quiet_from = [], [], None

    def close():
        if seg and (seg[-1].minute - seg[0].minute) / 60 >= QUIET_MIN_HOURS:
            out.append(list(seg))
    for i, r in enumerate(rows):
        busy = r.heating
        if not busy and r.bottom is not None:
            for j in range(i - 1, -1, -1):
                if r.minute - rows[j].minute > TAP_MINUTES:
                    break
                if rows[j].bottom is not None and rows[j].bottom - r.bottom >= TAP_DROP:
                    busy = True
                    break
        if busy:
            quiet_from = r.minute + SETTLE_MINUTES
        uneven = r.bottom is not None and r.top - r.bottom > EVEN_SPREAD and not seg
        if busy or (quiet_from is not None and r.minute < quiet_from) or uneven or (seg and r.minute - seg[-1].minute > 5):
            close()
            seg = []
            if not (busy or uneven or (quiet_from is not None and r.minute < quiet_from)):
                seg = [r]
        else:
            seg.append(r)
    close()
    return out


def loss_samples(rows: list[model.Row], *, volume: float, local_of) -> list[LossSample]:
    """The longest clean cooling stretch per day, as a heat-loss coefficient (W/K).

    ``local_of(minute)`` returns the local datetime of a row minute. Evenly hot tank: its mean temperature is
    simply the mean of top and bottom, without any assumption about layers."""
    best: dict[str, list[model.Row]] = {}
    for seg in quiet_stretches(rows):
        day = local_of(seg[-1].minute).date().isoformat()
        if day not in best or len(seg) > len(best[day]):
            best[day] = seg
    out = []
    for day, seg in sorted(best.items()):
        x = [(r.minute - seg[0].minute) / 60 for r in seg]
        mean = [(r.top + r.bottom) / 2 if r.bottom is not None else r.top for r in seg]
        average = sum(mean) / len(mean)
        if average - ROOM_TEMP < MIN_ABOVE_ROOM:
            continue
        rate = -_slope(list(zip(x, mean)))
        top_rate = -_slope([(h, r.top) for h, r in zip(x, seg)])
        bots = [(h, r.bottom) for h, r in zip(x, seg) if r.bottom is not None]
        bottom_rate = -_slope(bots) if len(bots) >= 2 else None
        ua = rate * volume * model.CP * 1000 / (average - ROOM_TEMP)
        out.append(LossSample(day=day, hours=round(x[-1], 1), average=round(average, 1), rate=round(rate, 3),
                              top_rate=round(top_rate, 3),
                              bottom_rate=round(bottom_rate, 3) if bottom_rate is not None else None, ua=round(ua, 3)))
    return out


def merge_samples(stored: list[dict], new: list[LossSample]) -> list[dict]:
    by = {n["day"]: n for n in stored}
    for n in new:                       # a day seen again in a shorter window keeps its longest stretch
        if n.day not in by or n.hours >= by[n.day]["hours"]:
            by[n.day] = n.as_dict()
    return [by[k] for k in sorted(by)][-LOSS_KEEP:]


def loss_status(samples: list[dict]) -> dict:
    """The last LOSS_RECENT measurements against the household's own baseline (the ones before them).

    raised: the heat-loss coefficient is clearly higher than it used to be (LOSS_RATIO, in at least 3 of the
    last LOSS_RECENT). leak_like: the bottom cools much faster relative to the top than it used to, the pattern
    of cold water trickling in through a dripping hot tap."""
    out = {"ready": False, "raised": False, "leak_like": False, "ua_baseline": None, "ua_recent": None,
           "ratio": None, "measurements": len(samples), "bottom_share_baseline": None, "bottom_share_recent": None}
    if not samples:
        return out
    out["ua_recent"] = round(statistics.median(n["ua"] for n in samples[-LOSS_RECENT:]), 3)
    if len(samples) < LOSS_BASELINE_DAYS + LOSS_RECENT:
        return out
    recent, base = samples[-LOSS_RECENT:], samples[:-LOSS_RECENT][-60:]
    b = statistics.median(n["ua"] for n in base)
    out.update(ready=True, ua_baseline=round(b, 3), ratio=round(out["ua_recent"] / b, 2) if b > 0 else None)
    out["raised"] = b > 0 and sum(1 for n in recent if n["ua"] >= b * LOSS_RATIO) >= 3

    def share(ns):
        vals = [n["bottom_rate"] / n["top_rate"] for n in ns
                if n.get("bottom_rate") is not None and n["top_rate"] > 0.05]
        return statistics.median(vals) if vals else None
    sb, sr = share(base), share(recent)
    out["bottom_share_baseline"] = round(sb, 2) if sb is not None else None
    out["bottom_share_recent"] = round(sr, 2) if sr is not None else None
    if sb is not None and sr is not None and sb > 0 and sr >= LEAK_BOTTOM_SHARE * sb and \
            sum(1 for n in recent if n.get("bottom_rate") is not None and n["top_rate"] > 0.05
                and n["bottom_rate"] / n["top_rate"] >= LEAK_BOTTOM_SHARE * sb) >= 3:
        out["leak_like"] = True
    return out


def loss_k_per_h(ua: float | None, tank_mean: float | None, volume: float) -> float | None:
    """How fast the tank cools now (K/h) from its measured heat-loss coefficient, or None when not measured."""
    if ua is None or tank_mean is None or volume <= 0:
        return None
    return max(0.0, ua * (tank_mean - ROOM_TEMP) / (volume * model.CP * 1000))


def heat_loss_watts(ua: float | None, tank_average: float | None) -> float | None:
    """What the tank loses right now, from the coefficient and how much warmer it is than the room."""
    if ua is None or tank_average is None:
        return None
    return round(max(0.0, ua * (tank_average - ROOM_TEMP)), 1)


def night_drips(rows: list[model.Row], *, local_of) -> list[dict]:
    """Per night: in how many of the hours DRIP_START-DRIP_END the bottom fell steadily faster than the top.

    A healthy tank at night: top and bottom cool together, or the bottom even warms (heat sinking down). A tap
    used at night: a quick drop within a few minutes. A dripping hot tap or a leak: cold water keeps coming in,
    so hour after hour the bottom falls more than the top, without a quick drop. Works from the first night."""
    hours: dict[tuple[str, int], list[model.Row]] = {}
    for r in rows:
        if r.bottom is None:
            continue
        t = local_of(r.minute)
        if DRIP_START <= t.hour < DRIP_END:
            hours.setdefault((t.date().isoformat(), t.hour), []).append(r)
    nights: dict[str, dict] = {}
    for (day, _h), rs in sorted(hours.items()):
        n = nights.setdefault(day, {"day": day, "hours": 0, "dripping": 0})
        if len(rs) < 50 or any(r.heating for r in rs):
            continue
        n["hours"] += 1
        drop_b, drop_t = rs[0].bottom - rs[-1].bottom, rs[0].top - rs[-1].top
        quick = max((rs[i - TAP_MINUTES].bottom - rs[i].bottom for i in range(TAP_MINUTES, len(rs))), default=0.0)
        if drop_b - drop_t >= DRIP_EXCESS and quick < 0.5 * drop_b:
            n["dripping"] += 1
    return [n for n in nights.values() if n["hours"] >= 3]


def merge_drips(stored: list[dict], new: list[dict]) -> list[dict]:
    by = {n["day"]: n for n in stored}
    for n in new:
        if n["day"] not in by or n["hours"] >= by[n["day"]]["hours"]:
            by[n["day"]] = n
    return [by[k] for k in sorted(by)][-30:]


def drip_status(nights: list[dict]) -> bool:
    """A steady drip in most measured hours, in DRIP_NIGHTS of the last 4 nights."""
    recent = nights[-4:]
    return sum(1 for n in recent if n["hours"] and n["dripping"] >= max(2, n["hours"] - 1)) >= DRIP_NIGHTS
