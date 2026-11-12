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

# Monthly cold (mains) water temperature, temperate climate (NL/BE/DE). Lowered per month by what the tank shows.
COLD_GENERIC = {"01": 8.0, "02": 8.0, "03": 9.5, "04": 11.5, "05": 14.0, "06": 16.5,
                "07": 18.5, "08": 19.0, "09": 18.0, "10": 15.5, "11": 12.5, "12": 9.5}
# The bottom sensor never reads colder than the mains water coming in, but after most uses it stays well above it
# (a shower lets in a fifth of the tank, which mixes with what was there): its coldest reading is an upper bound.
COLD_BELOW_BOTTOM = 1.0    # mains water is taken this far below the coldest bottom reading of a use
COLD_MAX_LOWER = 4.0       # the bottom lowers the profile's cold water by this much at most (a sensor reading low)
COLD_MIN_SAMPLES = 2       # days below the line before the line moves: one odd reading does not
SHOWER_FLOW = (4.0, 16.0)  # L/min a shower draws from the mains (hot and cold together)
SHOWER_FLOW_MINUTES = 3.0  # a shower's flow runs this long without a break; taps and washing up come in bursts

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
    p.setdefault("cold_samples", [])                  # [outdoor running average, coldest bottom of a day - 1]
    p.setdefault("reheat_rate", {"v": 0.3, "n": 0})   # tank average °C per minute while reheating
    if p.get("cold_version", 1) < 2:
        # Up to 0.13.1 the bottom after a shower was taken for the mains water itself, which pushed the cold water
        # up to 25 °C within days: back to the profile (what was learned downwards stays), refitted the new way.
        prof = PROFILES.get(p.get("profile"), PROFILES["generic"])
        p["cold"] = {m: min(v, prof["cold"].get(m, v)) for m, v in p["cold"].items()}
        p["cold_model"] = fit_cold_model(prof["cold_model"], p["cold_samples"])
        p["cold_version"] = 2
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
    temperature: cold = a + b * average, from the profile and lowered where the tank showed colder water. Without
    one it uses the monthly table, lowered the same way and smoothly interpolated."""
    if ema is not None:
        cm = params["cold_model"]
        return round(min(25.0, max(4.0, cm["a"]["v"] + cm["b"]["v"] * ema)), 2)
    return _cold_table(params, month, doy)



# --------------------------------------------------------------------------- layer model (sensor heights)
#
# A tank whose sensors are not at the very top and bottom holds hot water the sensors do not see. With their
# heights known the tank is a hot layer on cold water with a thermocline between them:
#     T(z) = cold + (hot - cold) * S((z - z0) / d),   z = share of the water column from the bottom, S = logistic
# z0 is placed to fit both readings (when they allow a range: the highest, fewest litres). The usable water is
# everything above the point where T falls to the shower temperature, counted as the mixed water it makes.

# Weheat spec sheet "Buffertanks & Boilertanks" (versions 280224, 080824 and 050225 agree), mm from the floor.
# WBL-200: the sensors sit higher than the sheet says (730 / 480 mm). Measured on a tank: top 330 mm below the top
# of the 1212 mm tank, bottom 580 mm up (#10); a second tank learned its top sensor at the same height.
TANK_TYPES: dict[str, dict] = {
    "weheat_wbl200": {"outlet": 1212, "top": 882, "bottom": 580, "inlet": 230},
    "weheat_wbl300": {"outlet": 1572, "top": 1220, "bottom": 560, "inlet": 230},
}
THERMOCLINE = 0.075        # d: width of the thermocline as a share of the column (measured 0.084 on a WBL-200)
LAYER_FIT_TOL = 0.5        # K: readings fitted this well count as fitting
GEO_MIN_DRAWS = 4          # showers that reached the thermocline before the top sensor's height moves
GEO_KEEP = 80              # showers kept for learning it


def new_geometry(tank_type: str | None, heights: dict | None, learn: bool) -> dict | None:
    """Sensor heights from what was typed in (``heights``: outlet, top, bottom and optionally inlet, mm from the
    floor) or else the tank type's spec sheet; None (the simple model) without either. The water column is taken
    to run from 110 mm below the cold inlet to 50 mm below the hot outlet (the domed ends hold little)."""
    h = heights if heights and all(heights.get(k) for k in ("outlet", "top", "bottom")) else None
    if h and not h["outlet"] > h["top"] > h["bottom"] > 0:
        h = None
    source = "manual" if h else tank_type
    h = h or TANK_TYPES.get(tank_type)
    if not h:
        return None
    inlet = h.get("inlet") or 230
    lo, hi = max(0.0, inlet - 110), h["outlet"] - 50
    z = lambda mm: round(min(0.98, max(0.0, (mm - lo) / max(hi - lo, 1.0))), 4)
    zt, zb = z(h["top"]), z(h["bottom"])
    return {"source": source, "learn": bool(learn), "start": [zt, zb], "inlet": z(inlet), "top": zt, "bottom": zb,
            "range": [round(max(zb + 0.1, zt - 0.1), 4), round(min(0.97, zt + 0.3), 4)], "draws": [], "n": 0}


def same_geometry_setup(a: dict | None, b: dict | None) -> bool:
    """Both from the same settings, so what was learned can stay."""
    key = lambda g: g and (g["source"], g["start"], g["learn"])
    return key(a) == key(b)


def _logistic(x: float) -> float:
    return 1.0 / (1.0 + math.exp(-max(-40.0, min(40.0, x))))


def _softplus(x: float) -> float:
    return x if x > 40 else math.log1p(math.exp(x))


def layer_temp(z: float, hot: float, cold: float, z0: float) -> float:
    return cold + (hot - cold) * _logistic((z - z0) / THERMOCLINE)


def fit_thermocline(top: float, bottom: float, hot: float, cold: float, zt: float, zb: float) -> float:
    best, best_err, highest = 0.0, 1e9, None
    for i in range(-40, 241):
        z0 = i / 200.0
        e = math.hypot(layer_temp(zt, hot, cold, z0) - top, layer_temp(zb, hot, cold, z0) - bottom)
        if e < best_err:
            best, best_err = z0, e
        if e <= LAYER_FIT_TOL:
            highest = z0
    return highest if highest is not None else best


def layer_litres(geo: dict, top: float, bottom: float, *, hot: float | None, cold: float, shower_temp: float,
                 volume: float) -> float:
    """Mixed litres at ``shower_temp`` the tank still makes. ``hot`` = the hottest the top was since the last
    reheat (less the standing loss): the water above the top sensor once the thermocline passed it. The integral
    of the logistic above the shower-temperature point (and above the cold inlet: the water below it stays)."""
    hot = max(hot or top, top)
    if hot <= shower_temp or shower_temp <= cold:
        return 0.0
    z0 = fit_thermocline(top, bottom, hot, cold, geo["top"], geo["bottom"])
    q = (shower_temp - cold) / (hot - cold)
    z_from = max(geo["inlet"], z0 + THERMOCLINE * math.log(q / (1 - q)))
    d = THERMOCLINE
    share = d * (_softplus((1 - z0) / d) - _softplus((z_from - z0) / d))
    return max(0.0, share) * volume / q


def _top_after(geo: dict, zt: float, draw: list, volume: float) -> float:
    """The top reading a shower should leave: the thermocline moves up by the hot water it drew."""
    bt, bb, _at, _ab, hot, cold, minutes, flow, ts = draw
    z0 = fit_thermocline(bt, bb, hot, cold, zt, geo["bottom"])
    drawn = flow * minutes * (ts - cold) / max(hot - cold, 1.0)
    return layer_temp(zt, hot, cold, z0 + drawn / volume)


def learn_geometry(geo: dict, volume: float) -> dict:
    """The top sensor's height from the showers in ``geo['draws']``: the height that best predicts the top
    reading after each shower (that it fell, or that it did not). Heights within 10 % of the best error count as
    good; of those the one nearest to where it is now, so it does not drift on noise. The bottom sensor stays: it
    sits in the zone the cold inlet stirs up, which the thermocline does not describe."""
    geo = copy.deepcopy(geo)
    # a draw: [top before, bottom before, top after, bottom after, hot, cold, minutes, flow, shower temp]
    deep = [d for d in geo["draws"] if d[1] < d[4] - 1.0 or d[3] < d[4] - 1.0]   # reached the thermocline
    geo["n"] = len(deep)
    if not geo["learn"] or len(deep) < GEO_MIN_DRAWS:
        return geo
    lo, hi = geo["range"]
    cur = geo["top"]
    errs = [(sum((_top_after(geo, z, d, volume) - d[2]) ** 2 for d in deep) / len(deep), z)
            for z in [lo + i * (hi - lo) / 20 for i in range(21)] + [cur]]
    best = min(e for e, _ in errs)
    geo["top"] = round(min((z for e, z in errs if e <= best * 1.1 + 0.01), key=lambda z: abs(z - cur)), 4)
    geo["error"] = round(math.sqrt(best), 2)
    return geo


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


def shower_minute(params: dict, cold: float, shower_temp: float, volume: float) -> float:
    """K the tank average falls per shower minute."""
    return params["flow"]["v"] * max(shower_temp - cold, 5.0) / volume


def estimate(params: dict, top: float, bottom: float | None, *, volume: float, shower_temp: float,
             month: int, head_flow: float | None = None, doy: int | None = None, ema: float | None = None,
             hot: float | None = None, draw_flow: float | None = None, blur: float | None = None) -> Estimate:
    """``shower_temp`` is the fixed temperature or the 38 °C reference in auto mode.
    ``head_flow`` (L/min) is the known shower-head flow, if any: it only changes the litres and the
    reported shower temperature, never the minutes (those come from the learned heat per minute).
    With sensor heights (``params['geo']``) and a bottom sensor the minutes come from the layer model; ``hot`` is
    then the hottest the top was since the last reheat (None: the top now), and the minutes are the litres over
    ``draw_flow`` (L/min a shower draws at ``shower_temp``, when known) or the learned flow. ``blur`` (see
    ``spread``) moves a part-used tank's minutes from the layer model towards the simple model the longer it stood.
    ``empty_at`` is the tank average the minutes run down to, so everything that counts in tank-average degrees
    keeps working."""
    cold = cold_for(params, month, doy, ema)
    flow = params["flow"]["v"]
    per_minute = shower_minute(params, cold, shower_temp, volume)
    empty = cold + params["phi"]["v"] * (shower_temp + 2 - cold)
    avg = tank_average(params, top, bottom)
    geo = params.get("geo")
    if geo and bottom is not None:
        rate = draw_flow or flow
        minutes = layer_litres(geo, top, bottom, hot=hot, cold=cold, shower_temp=shower_temp, volume=volume) / rate
        weight = (blur or 0.0) * part_used(top, bottom)
        if weight > 0.0:
            # a part-used tank that stood: its blurred boundary leaves the tank average as the better guide
            simple = max(0.0, (avg - empty) / per_minute) if top >= shower_temp + 2 else 0.0
            minutes += weight * (simple - minutes)
        empty = avg - minutes * per_minute
    else:
        minutes = max(0.0, (avg - empty) / per_minute) if top >= shower_temp + 2 else 0.0
    if bottom is None or top - bottom < 3:
        hot = 1.0 if top >= shower_temp + 2 else 0.0
    else:
        hot = min(1.0, max(0.0, (avg - bottom) / (top - bottom)))
    full = params.get("full_temp")
    if full and geo and bottom is not None:
        full_minutes = layer_litres(geo, full, full, hot=full, cold=cold, shower_temp=shower_temp,
                                    volume=volume) / (draw_flow or flow)
    else:
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


def _lowest_bottom(rows: list[Row], d: Draw) -> float | None:
    """Coldest bottom reading of a draw and the 10 minutes after it, outside a reheat (a heat pump starting up
    cools the bottom for a moment)."""
    lows = [r.bottom for r in rows if d.start <= r.minute <= d.start + d.minutes + 10 and not r.heating
            and r.bottom is not None]
    return min(lows) if lows else None


def _hot_before(rows: list[Row], minute: int, loss_k_h: float) -> float:
    """The hot layer before ``minute``: the hottest top since the last reheat (at most 24 h back), less the
    standing loss since then."""
    best, at = None, None
    for r in rows:
        if r.minute >= minute:
            break
        if r.minute < minute - 1440:
            continue
        if r.heating:
            best, at = None, None                  # a reheat: start again after it
            continue
        if best is None or r.top >= best:
            best, at = r.top, r.minute
    if best is None:
        prev = [r.top for r in rows if r.minute < minute]
        return prev[-1] if prev else 0.0
    return best - loss_k_h * (minute - at) / 60


SPREAD_HOURS = 6.0         # hours a part-used tank stands before its hot/cold boundary is mostly blurred (#10)


def part_used(top: float, bottom: float | None) -> float:
    """0 when hot water fills the tank past the bottom sensor (no boundary between the sensors), 1 when the
    bottom is 15 K or more below the top: cold water came in and the boundary sits between them."""
    if bottom is None:
        return 0.0
    return min(1.0, max(0.0, (top - bottom - 5.0) / 10.0))


def spread(top: float, bottom: float | None, prev: float | None, minutes: float,
           prev_share: float | None = None) -> float:
    """How far the boundary between hot and cold water has blurred: 0 = sharp, towards 1 the longer a part-used
    tank stands. Right after a shower the hot water sits on the cold water as a slab, as the layer model takes it.
    Hours later heat has spread down (the top cools faster than the standing loss, the bottom warms) and the same
    two readings mean much less hot water: a lukewarm middle instead of hot water down to the bottom sensor.
    Starts again once hot water fills the tank past the bottom sensor (a reheat). ``prev_share`` is how part-used
    the tank was at the previous reading: over a gap (a restart, a sensor away for hours) the tank only stood
    part-used as far as it was before the gap, not as a shower running right after it makes it look."""
    share = part_used(top, bottom)
    if share <= 0.0:
        return 0.0
    if prev_share is not None:
        share = min(share, prev_share)
    return 1.0 - (1.0 - (prev or 0.0)) * math.exp(-share * max(0.0, minutes) / 60.0 / SPREAD_HOURS)


def hot_layer(params: dict, top: float, hot: float | None, minutes: float, reheating: bool) -> float:
    """Follow the hot layer live: the top while reheating, else the hottest top since, less the standing loss."""
    if reheating or hot is None:
        return top
    return max(top, hot - params["loss"]["v"] * minutes / 60)


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
          doy_of=None, ema_of=None, draw_kind=None, draw_flow: float | None = None) -> tuple[LearnResult, Cycle | None]:
    """Update ``params`` with everything that happened after minute ``since``.

    ``month_of(minute) -> int`` maps a minute to the local month (1-12).
    ``water`` is an optional water meter: ("rate", [(minute, L/min)]) or ("total", [(minute, litres)]).
    ``doy_of(minute)`` (day of year) and ``ema_of(minute)`` (outdoor running average, or None) refine the
    cold water temperature; both are optional.
    ``draw_kind(draw) -> str`` (optional) tells showers from other draws ("tap", "fill", "coil": heat that left
    without water). Only showers then teach the shower length; for the flow the heat of taps is taken off, and an
    interval with heat leaving without water (or a bath) is not used. Every draw still keeps standing-loss
    blocks clean.
    ``draw_flow`` (L/min at ``shower_temp``) is how much a shower draws, for learning the sensor heights; None =
    the learned flow.
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
                          "loss": [], "duration": [], "head_flow": [], "cold": {}, "cold_pairs": [], "reheat_rate": [], "phi_upper": []}
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
        if has_bottom and c.bottom_before is not None:
            avg = tank_average(p, c.top_before, c.bottom_before)
            ratio = (avg - cold) / (shower_temp + 2 - cold)
            if c.top_before <= shower_temp + 1:
                s["phi"].append(min(1.1, max(0.3, ratio)))      # the hot water had really run out
            elif c.top_before >= shower_temp + 2:
                # a reheat on a timer or a hysteresis while the top could still give a shower: empty lies below
                # this, so it only caps phi (#14)
                s["phi_upper"].append(ratio)

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

    days: dict[int, list[float]] = {}
    for d in draws:
        if d.start < since:
            continue
        if kinds[d.start] == "shower":
            s["duration"].append(d.minutes)
        low = _lowest_bottom(rows, d) if kinds[d.start] != "coil" else None   # mains is at most this
        if low is None:
            continue
        m, cold = "%02d" % month_of(d.start), low - COLD_BELOW_BOTTOM
        s["cold"][m] = min(s["cold"].get(m, 99.0), cold)
        ema = ema_of(d.start) if ema_of else None
        day = d.start // 1440
        if ema is not None and (day not in days or cold < days[day][1]):
            days[day] = [round(ema, 2), round(cold, 2)]        # the coldest of each day
    s["cold_pairs"] = [days[k] for k in sorted(days)]

    if water:
        s["head_flow"] = water_flow_samples(water, [d for d in draws if d.start >= since and kinds[d.start] == "shower"])

    for key in W_POINTS:
        p["w"][key] = _blend(p["w"][key], s["w" + key], LIMITS["w"])
    for key in ("veff_factor", "phi", "flow", "loss", "duration", "head_flow", "reheat_rate"):
        p[key] = _blend(p[key], s[key], LIMITS[key])
    cap = statistics.median(s["phi_upper"]) if len(s["phi_upper"]) >= 2 else None
    if cap is not None and cap < p["phi"]["v"]:                 # two or more: one odd night does not lower it
        p["phi"]["v"] = round(max(LIMITS["phi"][0], cap), 4)

    if p.get("geo") and has_bottom:                  # sensor heights from the showers (layer model)
        geo = p["geo"]
        by = {r.minute: r for r in rows}
        for d in draws:
            if d.start < since or d.minutes < 5 or kinds[d.start] != "shower":
                continue
            b = by.get(d.start - 1) or next((r for r in reversed(rows) if r.minute < d.start), None)
            seg = [r for r in rows if d.start + d.minutes <= r.minute <= d.start + d.minutes + 10]
            if b is None or b.bottom is None or not seg or b.heating or any(r.heating for r in seg):
                continue
            a = min(seg, key=lambda r: tank_average(p, r.top, r.bottom))
            hot = _hot_before(rows, d.start, p["loss"]["v"])
            cold = cold_at(d.start)
            geo["draws"].append([b.top, b.bottom, a.top, a.bottom, round(hot, 2), round(cold, 2), d.minutes,
                                 round(draw_flow or p["flow"]["v"], 3), shower_temp])
        geo["draws"] = geo["draws"][-GEO_KEEP:]
        p["geo"] = learn_geometry(geo, volume)
    prof = PROFILES.get(p.get("profile"), PROFILES["generic"])
    for m, low in s["cold"].items():                 # an upper bound: it only ever lowers the month
        if low < p["cold"][m]:
            floor = min(p["cold"][m], prof["cold"].get(m, p["cold"][m]) - COLD_MAX_LOWER)
            p["cold"][m] = round(max(4.0, floor, 0.7 * p["cold"][m] + 0.3 * low), 1)

    if s["cold_pairs"]:
        p["cold_samples"] = (p["cold_samples"] + s["cold_pairs"])[-150:]
        p["cold_model"] = fit_cold_model(prof["cold_model"], p["cold_samples"])

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

    def flows(a: int, b: int) -> list[tuple[int, int, float]]:
        """(from, until, L/min) between a and b: a flow sensor's value holds until the next, a total's rate is
        the change between two readings."""
        if kind == "rate":
            pts = [(a, value_at(a))] + [(m, x) for m, x in series if a < m < b]
            return [(m, n, x) for (m, x), (n, _) in zip(pts, pts[1:] + [(b, None)]) if x is not None and n > m]
        pts = [(a, value_at(a))] + [(m, x) for m, x in series if a < m < b] + [(b, value_at(b))]
        pts = [(m, x) for m, x in pts if x is not None]
        return [(m, n, (y - x) / (n - m)) for (m, x), (n, y) in zip(pts, pts[1:]) if n > m and y >= x]

    lo, hi = SHOWER_FLOW
    for d in draws:
        if d.minutes < 4:
            continue
        # the tank keeps falling after the water stopped, and a tap used right after counts in the same draw: only
        # the longest unbroken shower-like flow in it is the shower head (#10)
        best: list[tuple[int, int, float]] = []
        run: list[tuple[int, int, float]] = []
        for seg in flows(d.start + 1, d.start + d.minutes):     # skip the first minute: the tank reacts late
            run = run + [seg] if lo <= seg[2] <= hi and (not run or run[-1][1] == seg[0]) else \
                ([seg] if lo <= seg[2] <= hi else [])
            if run and run[-1][1] - run[0][0] > (best[-1][1] - best[0][0] if best else 0):
                best = run
        if best and best[-1][1] - best[0][0] >= SHOWER_FLOW_MINUTES:
            out.append(sum((n - m) * x for m, n, x in best) / (best[-1][1] - best[0][0]))
    return [x for x in out if 2.0 <= x <= 25.0]


def fit_cold_model(prior: dict, samples: list[list[float]]) -> dict:
    """cold = a + b * outdoor average: the profile's line, lowered where the tank showed colder water.

    A sample is (outdoor average, coldest bottom reading of a day - 1): an upper bound for the mains water, so it
    can only pull the offset down and cannot tell the slope. ``prior`` is the profile's line (not last night's), so
    kept samples count once instead of again every night. The second lowest sample below the line sets the offset,
    so one odd reading does not, and never more than ``COLD_MAX_LOWER`` below the profile."""
    a0, b0 = prior["a"]["v"], prior["b"]["v"]
    below = sorted(y - b0 * x for x, y in samples if y - b0 * x < a0)
    a = max(a0 - COLD_MAX_LOWER, below[COLD_MIN_SAMPLES - 1]) if len(below) >= COLD_MIN_SAMPLES else a0
    return {"a": {"v": round(a, 3), "n": prior["a"]["n"]}, "b": {"v": b0, "n": prior["b"]["n"]}}


def shower_heat_kwh(per_minute: float, minutes: float, volume: float) -> float:
    """Heat taken from the tank by a shower: minutes x tank-average drop per minute x volume."""
    return max(0.0, minutes) * per_minute * volume * CP
