"""Self-learning hot water model for a stratified tank.

Pure Python (no Home Assistant imports) so it can be unit tested on its own.

The tank is treated as a hot layer floating on cold water: showering pushes cold
water in at the bottom and the hot layer leaves at the top. With a top and a
bottom sensor the tank average is estimated as

    avg = bottom + w(bottom) * (top - bottom)

where ``w`` (the stratification weight) depends on how cold the bottom is.
Hot water is "used up" when the outlet can no longer reach the shower
temperature; the tank average at that moment is

    empty = cold + phi * (shower_temp + 2 - cold)

and one minute of showering lowers the tank average by

    per_minute = flow * (shower_temp - cold) / volume      [K/min]

All parameters are learned nightly from the recorder history and blended with a
prior that only slowly gives way to new observations.
"""
from __future__ import annotations

import copy
import math
import statistics
from dataclasses import dataclass, field

CP = 1.163 / 1000          # kWh per litre per kelvin
MAX_PRIOR_WEIGHT = 60      # the prior never counts for more than this many observations
W_POINTS = ("20", "30", "45")

# Monthly cold (mains) water temperature, temperate climate (NL/BE/DE). Learned per month.
COLD_GENERIC = {"01": 8.0, "02": 8.0, "03": 9.5, "04": 11.5, "05": 14.0, "06": 16.5,
                "07": 18.5, "08": 19.0, "09": 18.0, "10": 15.5, "11": 12.5, "12": 9.5}

PROFILES: dict[str, dict] = {
    # Sensible starting point for any stratified tank with a top and bottom sensor.
    "generic": {
        "w": {"20": {"v": 0.65, "n": 10}, "30": {"v": 0.55, "n": 10}, "45": {"v": 0.42, "n": 10}},
        "flow": {"v": 7.5, "n": 10},
        "phi": {"v": 0.72, "n": 5},
        "loss": {"v": 0.30, "n": 10},
        "duration": {"v": 8.0, "n": 10},
        "veff_factor": {"v": 1.0, "n": 3},
        "cold": dict(COLD_GENERIC),
        # cold = a + b * 3-week outdoor average, for a correctly reading outdoor sensor (NL/BE network)
        "cold_model": {"a": {"v": 9.0, "n": 10}, "b": {"v": 0.6, "n": 10}},
    },
    # Learned from 13 months of minute data of a Weheat heat pump with the Weheat 200 L tank
    # (449 reheat cycles, 471 showers): see README.
    "weheat_200": {
        "w": {"20": {"v": 0.672, "n": 92}, "30": {"v": 0.577, "n": 137}, "45": {"v": 0.403, "n": 110}},
        "flow": {"v": 6.0, "n": 253},
        "phi": {"v": 0.73, "n": 9},
        "loss": {"v": 0.271, "n": 3939},
        "duration": {"v": 10.0, "n": 471},
        "veff_factor": {"v": 1.39, "n": 49},
        "cold": {"01": 10.0, "02": 10.6, "03": 12.1, "04": 13.6, "05": 16.1, "06": 17.2,
                 "07": 21.0, "08": 20.3, "09": 19.3, "10": 19.7, "11": 14.8, "12": 11.6},
        # fitted on 13 months against the Weheat outdoor sensor (which reads ~3 °C warm): rmse 0.65 °C
        "cold_model": {"a": {"v": 7.24, "n": 40}, "b": {"v": 0.604, "n": 40}},
    },
}

LIMITS = {"reheat_rate": (0.02, 3.0), "head_flow": (2.0, 25.0), "flow": (2.0, 20.0), "phi": (0.35, 0.95), "loss": (0.05, 1.0), "duration": (3.0, 30.0),
          "veff_factor": (0.6, 2.2), "w": (0.15, 0.95)}


def new_params(profile: str) -> dict:
    p = copy.deepcopy(PROFILES.get(profile, PROFILES["generic"]))
    p["profile"] = profile
    p["full_temp"] = None
    return ensure_params(p)


def ensure_params(p: dict) -> dict:
    """Add fields introduced in later versions to stored parameters."""
    p.setdefault("head_flow", {"v": 8.0, "n": 0})     # measured shower-head flow (water meter), L/min
    # cold water = a + b * outdoor running average (°C); default from the profile the model started with
    p.setdefault("cold_model", copy.deepcopy(PROFILES.get(p.get("profile"), PROFILES["generic"])["cold_model"]))
    p.setdefault("cold_samples", [])                  # [outdoor running average, measured cold] pairs
    p.setdefault("reheat_rate", {"v": 0.3, "n": 0})   # tank average °C per minute while reheating
    return p


# --------------------------------------------------------------------------- estimate

def w_at(params: dict, bottom: float) -> float:
    pts = [(float(k), params["w"][k]["v"]) for k in W_POINTS]
    if bottom <= pts[0][0]:
        return pts[0][1]
    for (b0, w0), (b1, w1) in zip(pts, pts[1:]):
        if bottom <= b1:
            return w0 + (w1 - w0) * (bottom - b0) / (b1 - b0)
    return pts[-1][1]


def tank_average(params: dict, top: float, bottom: float | None) -> float:
    if bottom is None:
        return top
    if top <= bottom:
        return (top + bottom) / 2
    return bottom + w_at(params, bottom) * (top - bottom)


def _cold_table(params: dict, month: int, doy: int | None) -> float:
    """Monthly table, interpolated between mid-month values so there is no jump on the 1st."""
    if doy is None:
        return params["cold"]["%02d" % month]
    pos = (doy - 15.2) / 30.44                  # 0 = mid January
    i = math.floor(pos)
    f = pos - i
    a = params["cold"]["%02d" % (i % 12 + 1)]
    b = params["cold"]["%02d" % ((i + 1) % 12 + 1)]
    return a + (b - a) * f


def cold_for(params: dict, month: int, doy: int | None = None, ema: float | None = None) -> float:
    """Cold (mains) water temperature.

    Mains water follows the ground, which follows the weather with a delay of weeks: a few warm days hardly
    matter, a cold month does. With an outdoor sensor the model uses a ~3-week running average of the outdoor
    temperature: cold = a + b * average, with a and b learned from long showers. Without one it uses the
    learned monthly table, smoothly interpolated."""
    if ema is not None:
        cm = params["cold_model"]
        return round(min(25.0, max(4.0, cm["a"]["v"] + cm["b"]["v"] * ema)), 2)
    return _cold_table(params, month, doy)


@dataclass
class Estimate:
    minutes: float
    liters: float
    showers: int
    average: float
    empty_at: float
    hot_fraction: float          # share of the tank that is still hot (for the card graphic)
    full_minutes: float | None   # minutes for a tank at the usual full temperature
    cold: float
    per_minute: float
    shower_temp: float           # temperature the litres refer to (set, sensed or the 38 °C reference)
    shower_temp_sensed: bool     # True when derived from a known shower-head flow
    flow: float                  # L/min the litres are based on


def sensed_shower_temp(params: dict, cold: float, ref_temp: float, head_flow: float) -> float:
    """Shower temperature from the learned heat per minute and a known shower-head flow.

    The tank only sees heat per minute = flow x (shower temp - cold). The model learns that as
    ``flow`` at ``ref_temp``; with the real flow known the temperature follows.
    """
    heat = params["flow"]["v"] * (ref_temp - cold)
    return min(46.0, max(30.0, cold + heat / head_flow))


def estimate(params: dict, top: float, bottom: float | None, *, volume: float, shower_temp: float,
             month: int, head_flow: float | None = None, doy: int | None = None, ema: float | None = None) -> Estimate:
    """``shower_temp`` is the fixed temperature or the 38 °C reference in auto mode.
    ``head_flow`` (L/min) is the known shower-head flow, if any: it only changes the litres and the
    reported shower temperature, never the minutes (those come from the learned heat per minute)."""
    cold = cold_for(params, month, doy, ema)
    flow = params["flow"]["v"]
    per_minute = flow * max(shower_temp - cold, 5.0) / volume
    empty = cold + params["phi"]["v"] * (shower_temp + 2 - cold)
    avg = tank_average(params, top, bottom)
    minutes = max(0.0, (avg - empty) / per_minute) if top >= shower_temp + 2 else 0.0
    if bottom is None or top - bottom < 3:
        hot = 1.0 if top >= shower_temp + 2 else 0.0
    else:
        hot = min(1.0, max(0.0, (avg - bottom) / (top - bottom)))
    full = params.get("full_temp")
    full_minutes = max(0.0, (full - empty) / per_minute) if full else None
    duration = params["duration"]["v"]
    temp, sensed, litre_flow = shower_temp, False, flow
    if head_flow:
        temp, sensed, litre_flow = sensed_shower_temp(params, cold, shower_temp, head_flow), True, head_flow
    return Estimate(minutes=minutes, liters=minutes * litre_flow, showers=int(minutes // duration), average=avg,
                    empty_at=empty, hot_fraction=hot, full_minutes=full_minutes, cold=cold,
                    per_minute=per_minute, shower_temp=temp, shower_temp_sensed=sensed, flow=litre_flow)


# --------------------------------------------------------------------------- learning

@dataclass
class Row:
    minute: int                 # minutes since epoch
    top: float
    bottom: float | None
    heating: bool


@dataclass
class Cycle:
    start: int
    end: int
    top_before: float
    bottom_before: float | None
    top_after: float
    bottom_after: float | None
    energy: float | None        # kWh delivered to the tank, if an energy sensor is configured


@dataclass
class Draw:
    start: int
    minutes: int
    top_start: float
    bottom_start: float | None
    bottom_low: float | None


def mark_heating_from_temperature(rows: list[Row]) -> None:
    """Without a heat-source entity: a reheat shows as a fast temperature rise."""
    for i in range(3, len(rows)):
        a, b = rows[i - 3], rows[i]
        if b.minute - a.minute > 6:
            continue
        rise_top = b.top - a.top
        rise_bot = (b.bottom - a.bottom) if (a.bottom is not None and b.bottom is not None) else 0.0
        if rise_top >= 0.6 or rise_bot >= 0.9:
            for r in rows[i - 3:i + 1]:
                r.heating = True


def find_cycles(rows: list[Row], energy_by_hour: dict[int, float] | None) -> list[Cycle]:
    spans, cur = [], None
    for i, r in enumerate(rows):
        if r.heating:
            if cur and r.minute - rows[cur[1]].minute <= 20:
                cur[1] = i
            else:
                if cur:
                    spans.append(cur)
                cur = [i, i]
    if cur:
        spans.append(cur)
    out = []
    for s, e in spans:
        if s == 0 or e + 20 >= len(rows):
            continue                              # cut off by the data window
        before, after = rows[s - 1], rows[e + 1:e + 20]
        t_after = max(x.top for x in after)
        b_vals = [x.bottom for x in after if x.bottom is not None]
        energy = None
        if energy_by_hour is not None:
            hours = range(rows[s].minute // 60 * 60, rows[e].minute // 60 * 60 + 61, 60)   # +1 h: cloud meters lag
            energy = sum(energy_by_hour.get(h, 0.0) for h in hours)
        out.append(Cycle(start=rows[s].minute, end=rows[e].minute, top_before=before.top,
                         bottom_before=before.bottom, top_after=t_after,
                         bottom_after=max(b_vals) if b_vals else None, energy=energy))
    return out


def find_draws(rows: list[Row]) -> list[Draw]:
    """Hot water use: the bottom (cold water entering) or the top falls quickly while not reheating."""
    draws, i = [], 1
    while i < len(rows):
        a, b = rows[i - 1], rows[i]
        fall_b = (a.bottom - b.bottom) if (a.bottom is not None and b.bottom is not None) else 0.0
        if not b.heating and b.minute - a.minute <= 3 and (fall_b >= 0.4 or a.top - b.top >= 0.4):
            s, e, quiet, k = i - 1, i, 0, i + 1
            while k < len(rows) and not rows[k].heating and quiet <= 3:
                p, q = rows[k - 1], rows[k]
                fb = (p.bottom - q.bottom) if (p.bottom is not None and q.bottom is not None) else 0.0
                if fb >= 0.15 or p.top - q.top >= 0.12:
                    e, quiet = k, 0
                else:
                    quiet += 1
                k += 1
            tail = [x.bottom for x in rows[e:e + 10] if x.bottom is not None]
            draws.append(Draw(start=rows[s].minute, minutes=rows[e].minute - rows[s].minute,
                              top_start=rows[s].top, bottom_start=rows[s].bottom,
                              bottom_low=min(tail) if tail else None))
            i = k
        else:
            i += 1
    out = []
    for d in draws:
        if d.minutes < 3:
            continue
        if d.bottom_start is not None and d.bottom_low is not None and d.bottom_start - d.bottom_low < 3:
            continue
        out.append(d)
    return out


def _draw_drop(params: dict, rows: list[Row], d: Draw) -> float:
    """Tank average drop (K) of a draw: its start against the lowest point in the 10 minutes after it."""
    seg = [r for r in rows if d.start <= r.minute <= d.start + d.minutes + 10 and not r.heating]
    if not seg:
        return 0.0
    a0 = tank_average(params, seg[0].top, seg[0].bottom)
    return max(0.0, a0 - min(tank_average(params, r.top, r.bottom) for r in seg))


def _blend(old: dict, samples: list[float], limits: tuple[float, float]) -> dict:
    if not samples:
        return old
    n = min(old["n"], MAX_PRIOR_WEIGHT)
    m = len(samples)
    v = (n * old["v"] + m * statistics.median(samples)) / (n + m)
    return {"v": round(min(max(v, limits[0]), limits[1]), 4), "n": old["n"] + m}


@dataclass
class LearnResult:
    params: dict
    samples: dict = field(default_factory=dict)
    cycles: int = 0
    draws: int = 0


def learn(params: dict, rows: list[Row], *, volume: float, shower_temp: float, since: int,
          energy_by_hour: dict[int, float] | None, previous_cycle: Cycle | None,
          month_of, water: tuple[str, list[tuple[int, float]]] | None = None,
          doy_of=None, ema_of=None, draw_kind=None) -> tuple[LearnResult, Cycle | None]:
    """Update ``params`` with everything that happened after minute ``since``.

    ``month_of(minute) -> int`` maps a minute to the local month (1-12).
    ``water`` is an optional water meter: ("rate", [(minute, L/min)]) or ("total", [(minute, litres)]).
    ``doy_of(minute)`` (day of year) and ``ema_of(minute)`` (outdoor running average, or None) refine the
    cold water temperature; both are optional.
    ``draw_kind(draw) -> str`` (optional) tells showers from other draws ("tap", "fill", "coil": heat that left
    without water). Only showers then teach the shower length; for the flow the heat of taps is taken off, and an
    interval with heat leaving without water (or a bath) is not used. Every draw still keeps standing-loss
    blocks clean.
    Returns the result and the last complete cycle (kept for the next run).
    """
    p = ensure_params(copy.deepcopy(params))

    def cold_at(minute: int) -> float:
        return cold_for(p, month_of(minute), doy_of(minute) if doy_of else None, ema_of(minute) if ema_of else None)

    if not any(r.heating for r in rows):
        mark_heating_from_temperature(rows)
    has_bottom = any(r.bottom is not None for r in rows)
    cycles = find_cycles(rows, energy_by_hour)
    draws = find_draws(rows)
    kinds = {d.start: (draw_kind(d) if draw_kind else "shower") for d in draws}
    s: dict[str, list] = {"w20": [], "w30": [], "w45": [], "veff_factor": [], "phi": [], "flow": [],
                          "loss": [], "duration": [], "head_flow": [], "cold": {}, "cold_pairs": [], "reheat_rate": []}
    full = lambda c: c.bottom_after is None or c.top_after - c.bottom_after <= 2.5
    veff = volume * p["veff_factor"]["v"]
    # An energy meter is only trusted for stratification and flow once its scale against the tank is known;
    # many heat pumps report more heat than reaches the tank (piping, coil, meter).
    energy_ok = p["veff_factor"]["n"] >= 15

    for c in cycles:                              # how fast a reheat raises the tank average (any reheat)
        if c.start >= since and c.end - c.start >= 10 and c.bottom_before is not None:
            rise = tank_average(p, c.top_after, c.bottom_after) - tank_average(p, c.top_before, c.bottom_before)
            if rise > 2:
                s["reheat_rate"].append(rise / (c.end - c.start))

    for c in cycles:
        if c.start < since or not full(c):
            continue
        cold = cold_at(c.start)
        if c.energy is not None and c.energy >= 0.2 and c.bottom_before is not None:
            if c.top_before - c.bottom_before < 3 and c.top_after - c.top_before >= 4:
                start_avg = (c.top_before + c.bottom_before) / 2
                s["veff_factor"].append(c.energy / (CP * (c.top_after - start_avg)) / volume)
            if energy_ok and c.top_before - c.bottom_before >= 5:
                avg = c.top_after - c.energy / (CP * veff)
                w = (avg - c.bottom_before) / (c.top_before - c.bottom_before)
                key = "w20" if c.bottom_before < 25 else ("w30" if c.bottom_before < 35 else "w45")
                s[key].append(w)
        if has_bottom and c.bottom_before is not None and c.top_before <= shower_temp + 5:
            avg = tank_average(p, c.top_before, c.bottom_before)
            s["phi"].append(min(1.1, max(0.3, (avg - cold) / (shower_temp + 2 - cold))))

    # heat drawn between two full reheats against the visible shower minutes -> flow
    chain = sorted({c.start: c for c in ([previous_cycle] if previous_cycle else []) +
                    [c for c in cycles if full(c)]}.values(), key=lambda c: c.start)
    for a, b in zip(chain, chain[1:]):
        if b.start < since or not has_bottom:
            continue
        hours = (b.start - a.end - 20) / 60
        if not 3 <= hours <= 40:
            continue
        if any(a.end < c.start < b.start for c in cycles):
            continue                              # a partial top-up in between: its heat is not in the sum
        inside = [d for d in draws if a.end + 20 <= d.start < b.start]
        cold_b = cold_at(b.start)
        if not inside or any(d.bottom_low is not None and d.bottom_low < 30 for d in inside[:-1]):
            continue                              # a later draw could have been invisible
        if any(kinds[d.start] not in ("shower", "tap") for d in inside):
            continue                              # heat that left without water, or a bath: no shower measure
        taps = [d for d in inside if kinds[d.start] == "tap"]
        inside = [d for d in inside if kinds[d.start] == "shower"]
        if not inside:
            continue
        if inside[-1].bottom_low is not None and inside[-1].bottom_low < cold_b + 3:
            continue                              # bottom hit mains temperature: the end of the shower was invisible
        minutes = sum(d.minutes for d in inside)
        if minutes < 4:
            continue
        if energy_ok and b.energy is not None and b.energy >= 0.2:
            drawn = (a.top_after - b.top_after) + b.energy / (CP * veff)
        else:
            drawn = tank_average(p, a.top_after, a.bottom_after) - tank_average(p, b.top_before, b.bottom_before)
        drawn -= p["loss"]["v"] * hours
        drawn -= sum(_draw_drop(p, rows, d) for d in taps)      # washing up took a little heat too
        cold = cold_at(b.start)
        if drawn > 0:
            s["flow"].append(drawn * volume / (minutes * (shower_temp - cold)))

    # standing loss: 6-hour blocks without reheating or showering
    busy = [(d.start - 10, d.start + d.minutes + 10) for d in draws]
    i = 0
    while i < len(rows):
        j = i
        while j < len(rows) and rows[j].minute < rows[i].minute + 360:
            j += 1
        if j >= len(rows):
            break
        block = rows[i:j + 1]
        if block[0].minute >= since and block[-1].minute - block[0].minute <= 380 \
                and not any(r.heating for r in block) \
                and not any(a < block[-1].minute and b > block[0].minute for a, b in busy):
            t0 = tank_average(p, block[0].top, block[0].bottom)
            t1 = tank_average(p, block[-1].top, block[-1].bottom)
            s["loss"].append((t0 - t1) / ((block[-1].minute - block[0].minute) / 60))
        i += 60

    for d in draws:
        if d.start < since or kinds[d.start] != "shower":
            continue
        s["duration"].append(d.minutes)
        if d.bottom_start is not None and d.bottom_low is not None and d.bottom_start - d.bottom_low >= 15:
            m = "%02d" % month_of(d.start)
            s["cold"][m] = min(s["cold"].get(m, 99.0), d.bottom_low - 1)
            ema = ema_of(d.start) if ema_of else None
            if ema is not None:
                s["cold_pairs"].append([round(ema, 2), round(d.bottom_low - 1, 2)])

    if water:
        s["head_flow"] = water_flow_samples(water, [d for d in draws if d.start >= since and kinds[d.start] == "shower"])

    for key in W_POINTS:
        p["w"][key] = _blend(p["w"][key], s["w" + key], LIMITS["w"])
    for key in ("veff_factor", "phi", "flow", "loss", "duration", "head_flow", "reheat_rate"):
        p[key] = _blend(p[key], s[key], LIMITS[key])
    for m, low in s["cold"].items():                 # cold water only moves towards what was seen
        p["cold"][m] = round(min(max(4.0, 0.7 * p["cold"][m] + 0.3 * low), 25.0), 1)

    if s["cold_pairs"]:
        p["cold_samples"] = (p["cold_samples"] + s["cold_pairs"])[-150:]
        p["cold_model"] = fit_cold_model(p["cold_model"], p["cold_samples"])

    tops = [c.top_after for c in cycles if full(c)]
    if tops:
        usual = statistics.median(tops)
        p["full_temp"] = round(usual if p.get("full_temp") is None else 0.8 * p["full_temp"] + 0.2 * usual, 1)
        # the tank average a complete reheat ends at (what "full again at" counts towards); the median leaves
        # out the odd hotter legionella heat-up
        avgs = [tank_average(p, c.top_after, c.bottom_after) for c in cycles if full(c) and c.bottom_after is not None]
        if avgs:
            usual = statistics.median(avgs)
            p["full_avg"] = round(usual if p.get("full_avg") is None else 0.8 * p["full_avg"] + 0.2 * usual, 1)

    last = next((c for c in reversed(cycles) if full(c)), previous_cycle)
    counts = {k: (len(v) if isinstance(v, list) else len(v)) for k, v in s.items()}
    return LearnResult(params=p, samples=counts, cycles=len(cycles), draws=len(draws)), last


def water_flow_samples(water: tuple[str, list[tuple[int, float]]], draws: list[Draw]) -> list[float]:
    """Shower-head flow (L/min) seen by a water meter during each detected shower."""
    kind, series = water
    series = sorted(series)
    out = []

    def value_at(minute: int) -> float | None:
        v = None
        for m, x in series:
            if m > minute:
                break
            v = x
        return v

    for d in draws:
        if d.minutes < 4:
            continue
        a, b = d.start + 1, d.start + d.minutes          # skip the first minute: the tank reacts a bit late
        if kind == "rate":
            vals = [x for m, x in series if a <= m <= b and x > 0.5]
            if len(vals) >= 2:
                out.append(statistics.median(vals))
        else:
            v0, v1 = value_at(a), value_at(b)
            if v0 is not None and v1 is not None and v1 > v0:
                out.append((v1 - v0) / (b - a))
    return [x for x in out if 2.0 <= x <= 25.0]


def fit_cold_model(prior: dict, samples: list[list[float]]) -> dict:
    """Learn cold = a + b * outdoor average from (average, measured cold) pairs.

    The slope is only learned once the samples span at least 4 °C of outdoor average (i.e. over the seasons);
    until then only the offset moves. Both stay blended with the prior so a handful of showers cannot swing it."""
    a0, b0 = prior["a"]["v"], prior["b"]["v"]
    n = len(samples)
    if n == 0:
        return prior
    xs = [x for x, _ in samples]
    ys = [y for _, y in samples]
    if n >= 8 and max(xs) - min(xs) >= 4:
        mx, my = statistics.mean(xs), statistics.mean(ys)
        sxx = sum((x - mx) ** 2 for x in xs)
        b = sum((x - mx) * (y - my) for x, y in zip(xs, ys)) / sxx
        b = min(1.0, max(0.3, b))
        a = my - b * mx
    else:
        b = b0
        a = statistics.median([y - b0 * x for x, y in zip(xs, ys)])
    w0 = min(prior["a"]["n"], MAX_PRIOR_WEIGHT)
    blend = lambda old, new: (w0 * old + n * new) / (w0 + n)
    return {"a": {"v": round(blend(a0, a), 3), "n": prior["a"]["n"]},
            "b": {"v": round(blend(b0, b), 3), "n": prior["b"]["n"]}}


def shower_heat_kwh(per_minute: float, minutes: float, volume: float) -> float:
    """Heat taken from the tank by a shower: minutes x tank-average drop per minute x volume."""
    return max(0.0, minutes) * per_minute * volume * CP
