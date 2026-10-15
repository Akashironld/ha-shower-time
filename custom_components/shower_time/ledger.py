"""Shower log, cost per shower and the reheat forecast.

Cost with dynamic tariffs: electricity is bought when the tank is *reheated*, not when you shower. The
ledger therefore keeps a price for the hot water in the tank (currency per kWh of heat): every bit of heat a
reheat adds is booked at the electricity price of that moment divided by the COP, blended with the heat that
was already in the tank. A shower costs the heat it takes out times that blended price.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
import logging
from typing import TYPE_CHECKING

from homeassistant.components.recorder import get_instance, statistics
from homeassistant.const import STATE_UNAVAILABLE, STATE_UNKNOWN
from homeassistant.util import dt as dt_util

from . import insights, model
from .const import DEFAULT_COP, LOG_DAYS, MAX_SHOWER_MINUTES, MIN_SHOWER_MINUTES

if TYPE_CHECKING:
    from .manager import ShowerTimeManager

_LOGGER = logging.getLogger(__name__)


def parse_price(state) -> tuple[float | None, str]:
    """Electricity price per kWh in main currency units, and the currency symbol."""
    if state is None or state.state in (STATE_UNAVAILABLE, STATE_UNKNOWN):
        return None, ""
    try:
        value = float(state.state)
    except (TypeError, ValueError):
        return None, ""
    unit = str(state.attributes.get("unit_of_measurement") or "")
    cur = unit.split("/")[0].strip()
    low = cur.lower()
    if low in ("ct", "c", "cent", "cents", "¢"):         # cents per kWh
        return value / 100, "€"
    if low in ("eur", "euro"):
        cur = "€"
    if "mwh" in unit.lower():
        value /= 1000
    return value, cur


class ShowerLedger:
    def __init__(self) -> None:
        self.log: list[dict] = []
        self.tank_price: float | None = None        # currency per kWh of heat in the tank
        self.currency: str = ""
        self.cop_learned: float | None = None
        self.backfilled = False
        self.session_start: datetime | None = None
        self._avg_prev: float | None = None
        self._heat_start: tuple[datetime, float] | None = None   # when the reheat going on now began, tank average
        self.eta_full: datetime | None = None
        self.eta_one: datetime | None = None
        # running totals for Home Assistant's long-term statistics (showers logged live, never reset)
        self.totals: dict = {"showers": 0, "minutes": 0.0, "heat_kwh": 0.0, "electricity_kwh": 0.0, "cost": 0.0}
        self.covered_from: str | None = None        # local date from which the log holds every shower
        self.other: list[dict] = []                 # other hot water use: washing up, taps, a bath being filled

    # ------------------------------------------------------------------ persistence
    def to_dict(self) -> dict:
        return {"log": self.log, "tank_price": self.tank_price, "currency": self.currency,
                "cop_heat_pump": self.cop_learned, "backfilled": self.backfilled, "totals": self.totals,
                "covered_from": self.covered_from, "other": self.other}

    def load(self, d: dict | None) -> None:
        if not d:
            return
        self.log = d.get("log", [])
        self.tank_price = d.get("tank_price")
        self.currency = d.get("currency", "")
        self.cop_learned = d.get("cop_heat_pump")     # (before 0.11.2 "cop_learned" held the tank COP)
        self.backfilled = d.get("backfilled", False)
        self.totals.update(d.get("totals") or {})
        self.covered_from = d.get("covered_from")
        self.other = d.get("other", [])
        if self.covered_from is None and self.log:   # logs from before 0.10: complete from the day after the first one
            first = datetime.fromisoformat(self.log[0]["start"]).date()
            self.covered_from = (first + timedelta(days=1)).isoformat()

    # ------------------------------------------------------------------ COP and price
    def cop_heat_pump(self, m: ShowerTimeManager) -> tuple[float, str]:
        """The heat pump's COP for hot water as its own meters (and app) see it, and where it comes from: filled
        in by the user, learned from the heat and electricity meters, or the default."""
        if m.cop_manual:
            return m.cop_manual, "manual"
        if self.cop_learned:
            return self.cop_learned, "learned"
        return DEFAULT_COP, "default"

    def cop(self, m: ShowerTimeManager) -> float:
        """COP per kWh of heat that reaches the tank: many heat pumps' heat meters count more than arrives (pipes,
        coil); the model learned that ratio (veff_factor). This is what the cost of hot water is computed with."""
        veff = m.params["veff_factor"]["v"]
        return round(self.cop_heat_pump(m)[0] / (veff if veff > 0 else 1.0), 2)

    def price_now(self, m: ShowerTimeManager) -> tuple[float | None, str]:
        if not m.price:
            return None, ""
        return parse_price(m.hass.states.get(m.price))

    async def async_refresh_cop(self, m: ShowerTimeManager) -> None:
        """The heat pump's COP for hot water from its heat and electricity meters over 30 days (as its app shows
        it; ``cop`` turns it into a COP per kWh in the tank)."""
        if not (m.elec and m.energy):
            return
        end = dt_util.utcnow()
        start = end - timedelta(days=30)
        try:
            stats = await get_instance(m.hass).async_add_executor_job(
                statistics.statistics_during_period, m.hass, start, end, {m.elec, m.energy}, "day",
                {"energy": "kWh"}, {"change"})
            heat = sum(float(r.get("change") or 0) for r in stats.get(m.energy, []))
            elec = sum(float(r.get("change") or 0) for r in stats.get(m.elec, []))
            if elec > 3 and heat > elec:
                self.cop_learned = round(heat / elec, 2)
        except Exception:  # noqa: BLE001
            _LOGGER.exception("Could not compute the hot water COP")

    # ------------------------------------------------------------------ live hooks
    def on_estimate(self, m: ShowerTimeManager, est: model.Estimate) -> None:
        """Book heat added by a reheat at the current price; update the reheat forecast."""
        avg = est.average
        prev = self._avg_prev
        self._avg_prev = avg
        heating = m.reheating or (prev is not None and avg - prev >= 0.3 and not m.showering)
        if heating and prev is not None and avg > prev:
            price, cur = self.price_now(m)
            if price is not None:
                add = (avg - prev) * m.volume * model.CP
                held = max(0.0, prev - est.empty_at) * m.volume * model.CP
                heat_price = price / self.cop(m)
                if self.tank_price is None or held <= 0:
                    self.tank_price = heat_price
                else:
                    self.tank_price = (held * self.tank_price + add * heat_price) / (held + add)
                self.currency = cur or self.currency
                m.request_save()
        self._forecast(m, est, heating)

    def _forecast(self, m: ShowerTimeManager, est: model.Estimate, heating: bool) -> None:
        """When the tank is full again, and when it holds one shower, while a reheat runs.

        The pace is the one this reheat shows, once it has run a while (a steady heat pump keeps its pace; the
        learned average also holds slow and boosted reheats), blended with the learned pace in its first 20
        minutes. The target is the usual tank average after a complete reheat."""
        self.eta_full = self.eta_one = None
        now = dt_util.now()
        if not heating:
            self._heat_start = None
            return
        if self._heat_start is None:
            self._heat_start = (now, est.average)
        rate = m.params["reheat_rate"]
        learned = rate["v"] if rate["n"] >= 2 and rate["v"] > 0 else None
        start, avg0 = self._heat_start
        minutes = (now - start).total_seconds() / 60
        live = (est.average - avg0) / minutes if minutes >= 8 and est.average > avg0 + 1 else None
        if live is None and learned is None:
            return
        w = 1.0 if learned is None else (0.0 if live is None else min(1.0, minutes / 20))
        pace = w * (live or 0.0) + (1 - w) * (learned or 0.0)
        if pace <= 0:
            return
        target = m.params.get("full_avg") or (m.params["full_temp"] - 1.0 if m.params.get("full_temp") else m.full_ref_avg)
        if target and target > est.average:
            self.eta_full = now + timedelta(minutes=(target - est.average) / pace)
        need = est.empty_at + m.params["duration"]["v"] * est.per_minute
        self.eta_one = now if est.average >= need else now + timedelta(minutes=(need - est.average) / pace)

    def shower_ended(self, m: ShowerTimeManager, start: datetime, end: datetime,
                     est: model.Estimate | None, min_minutes: float = MIN_SHOWER_MINUTES) -> dict | None:
        """Log a finished shower; returns the log entry, or None when it was too short or too long."""
        minutes = (end - start).total_seconds() / 60
        if not min_minutes <= minutes <= MAX_SHOWER_MINUTES or est is None:
            return None
        return self._add(m, start, minutes, est.per_minute, self.tank_price, "live")

    def running(self, m: ShowerTimeManager, now: datetime) -> dict | None:
        """The shower going on right now (once recognised as one): minutes so far and what that hot water cost."""
        if not m.showering or self.session_start is None or m.estimate is None or m.use_kind() != "shower":
            return None
        minutes = max(0.0, (now - self.session_start).total_seconds() / 60)
        heat = model.shower_heat_kwh(m.estimate.per_minute, minutes, m.volume)
        price = self.tank_price
        if price is None:
            p, _ = self.price_now(m)
            price = p / self.cop(m) if p is not None else None
        return {"started": dt_util.as_local(self.session_start).isoformat(timespec="seconds"),
                "minutes": round(minutes, 1), "heat_kwh": round(heat, 2),
                "electricity_kwh": round(heat / self.cop(m), 2),
                "cost": round(heat * price, 2) if price is not None else None}

    def bath_ended(self, m: ShowerTimeManager, start: datetime, duration: float, minutes: float,
                   est: model.Estimate) -> dict:
        """Log a bath as a session: ``minutes`` is its heat in shower minutes, ``duration`` how long it ran."""
        return self._add(m, start, minutes, est.per_minute, self.tank_price, "live", kind="bath",
                         duration=round(duration, 1))

    def estimated_ended(self, m: ShowerTimeManager, start: datetime, minutes: float, est: model.Estimate) -> dict:
        """Log a session hidden by a gap in the tank readings: its length follows from the heat it took."""
        return self._add(m, start, minutes, est.per_minute, self.tank_price, "live", estimated=True)

    def _add(self, m: ShowerTimeManager, start: datetime, minutes: float, per_minute: float,
             heat_price: float | None, source: str, kind: str = "shower", **extra) -> dict:
        heat = model.shower_heat_kwh(per_minute, minutes, m.volume)
        cop = self.cop(m)
        if heat_price is None:
            price, cur = self.price_now(m)
            heat_price = price / cop if price is not None else None
            self.currency = cur or self.currency
        entry = {"start": dt_util.as_local(start).isoformat(timespec="minutes"), "minutes": round(minutes, 1),
                 "heat_kwh": round(heat, 2), "electricity_kwh": round(heat / cop, 2),
                 "cost": round(heat * heat_price, 2) if heat_price is not None else None, "source": source,
                 "kind": kind, **extra}
        self.log.append(entry)
        self.log.sort(key=lambda e: e["start"])
        cutoff = (dt_util.now() - timedelta(days=LOG_DAYS)).isoformat()
        self.log = [e for e in self.log if e["start"] >= cutoff]
        if source == "live":
            t = self.totals
            t["showers"] += 1
            t["minutes"] = round(t["minutes"] + entry["minutes"], 1)
            t["heat_kwh"] = round(t["heat_kwh"] + entry["heat_kwh"], 3)
            t["electricity_kwh"] = round(t["electricity_kwh"] + entry["electricity_kwh"], 3)
            if entry["cost"] is not None:
                t["cost"] = round(t["cost"] + entry["cost"], 2)
        m.request_save()
        return entry

    def other_ended(self, m: ShowerTimeManager, kind: str, start: datetime, minutes: float, heat_kwh: float,
                    **extra) -> dict:
        """Log hot water that was not a shower (washing up, a tap, a bath or bucket), with what it cost."""
        cop = self.cop(m)
        price = self.tank_price
        if price is None:
            p, cur = self.price_now(m)
            price = p / cop if p is not None else None
            self.currency = cur or self.currency
        entry = {"start": dt_util.as_local(start).isoformat(timespec="minutes"), "kind": kind,
                 "minutes": round(minutes, 1), "heat_kwh": round(heat_kwh, 2),
                 "electricity_kwh": round(heat_kwh / cop, 2),
                 "cost": round(heat_kwh * price, 2) if price is not None else None, **extra}
        cutoff = (dt_util.now() - timedelta(days=LOG_DAYS)).isoformat()
        self.other = [e for e in self.other if e["start"] >= cutoff] + [entry]
        m.request_save()
        return entry

    def other_today(self) -> dict:
        today = dt_util.now().replace(hour=0, minute=0, second=0, microsecond=0).isoformat(timespec="minutes")
        t = [e for e in self.other if e["start"] >= today]
        costs = [e["cost"] for e in t if e.get("cost") is not None]
        kinds: dict[str, int] = {}
        for e in t:
            kinds[e["kind"]] = kinds.get(e["kind"], 0) + 1
        return {"count": len(t), "heat_kwh": round(sum(e["heat_kwh"] for e in t), 2),
                "electricity_kwh": round(sum(e["electricity_kwh"] for e in t), 2),
                "cost": round(sum(costs), 2) if costs else (0.0 if not t else None), "by_kind": kinds}

    # ------------------------------------------------------------------ summaries
    def _since(self, since: datetime) -> list[dict]:
        key = since.isoformat(timespec="minutes")
        return [e for e in self.log if e["start"] >= key]

    def summary(self) -> dict:
        now = dt_util.now()
        today = now.replace(hour=0, minute=0, second=0, microsecond=0)
        week = today - timedelta(days=today.weekday())
        t, w = self._since(today), self._since(week)
        costs_t = [e["cost"] for e in t if e.get("cost") is not None]
        costs_w = [e["cost"] for e in w if e.get("cost") is not None]
        return {
            "today_showers": len(t), "today_minutes": round(sum(e["minutes"] for e in t)),
            "today_cost": round(sum(costs_t), 2) if costs_t else (0.0 if not t else None),
            "today_electricity_kwh": round(sum(e["electricity_kwh"] for e in t), 2),
            "week_showers": len(w), "week_minutes": round(sum(e["minutes"] for e in w)),
            "week_cost": round(sum(costs_w), 2) if costs_w else (0.0 if not w else None),
            "last": self.log[-1] if self.log else None,
        }

    def needs_reprice(self, m: ShowerTimeManager) -> bool:
        """A price sensor was added after the log was filled from the history: price those entries again."""
        return bool(m.price) and any(e.get("cost") is None and e.get("source") == "history" for e in self.log)

    def reset_history(self) -> None:
        self.log = [e for e in self.log if e.get("source") != "history"]
        self.backfilled = False

    # ------------------------------------------------------------------ first run: fill from the history
    async def async_backfill(self, m: ShowerTimeManager, rows: list[model.Row]) -> None:
        """Showers of the past week from the recorder, priced at the last reheat before each shower."""
        if self.backfilled or not rows:
            return
        cycles = model.find_cycles(rows, None)
        def cold_at(minute: int) -> float:
            local = dt_util.as_local(datetime.fromtimestamp(minute * 60, timezone.utc))
            return model.cold_for(m.params, local.month, local.timetuple().tm_yday, m._ema_on(local.date().isoformat()))
        # showers only: not washing up, and not heat that left without water (a heat pump starting a reheat)
        draws = [d for d in model.find_draws(rows) if MIN_SHOWER_MINUTES <= d.minutes <= MAX_SHOWER_MINUTES
                 and insights.draw_kind(m.params, rows, d.start, d.minutes, cold_at(d.start), m.confirm_k(),
                                        m.fill_rate()) == "shower"]
        price_by_hour: dict[int, float] = {}
        cur = ""
        if m.price:
            st = m.hass.states.get(m.price)
            _, cur = parse_price(st)
            factor = 1.0
            if st is not None:
                unit = str(st.attributes.get("unit_of_measurement") or "").split("/")[0].strip().lower()
                factor = 0.01 if unit in ("ct", "c", "cent", "cents", "¢") else 1.0
            start = datetime.fromtimestamp(rows[0].minute * 60, timezone.utc)
            end = datetime.fromtimestamp(rows[-1].minute * 60, timezone.utc)
            try:
                stats = await get_instance(m.hass).async_add_executor_job(
                    statistics.statistics_during_period, m.hass, start, end, {m.price}, "hour", None, {"mean"})
                for r in stats.get(m.price, []):
                    ts = r["start"]
                    ts = ts.timestamp() if isinstance(ts, datetime) else float(ts)
                    if r.get("mean") is not None:
                        price_by_hour[int(ts // 60) // 60 * 60] = float(r["mean"]) * factor
            except Exception:  # noqa: BLE001
                _LOGGER.debug("No price statistics for the backfill")
        cop = self.cop(m)
        known = {e["start"] for e in self.log}
        for d in draws:
            before = [c for c in cycles if c.end < d.start]
            prices = []
            if before:
                c = before[-1]
                prices = [price_by_hour[h] for h in range(c.start // 60 * 60, c.end // 60 * 60 + 1, 60) if h in price_by_hour]
            if not prices and (d.start // 60 * 60) in price_by_hour:
                prices = [price_by_hour[d.start // 60 * 60]]
            heat_price = (sum(prices) / len(prices)) / cop if prices else None
            start = datetime.fromtimestamp(d.start * 60, timezone.utc)
            if dt_util.as_local(start).isoformat(timespec="minutes") in known:
                continue
            local = dt_util.as_local(start)
            est = model.estimate(m.params, d.top_start, d.bottom_start, volume=m.volume, shower_temp=m.shower_temp,
                                 month=local.month, doy=local.timetuple().tm_yday,
                                 ema=m._ema_on(local.date().isoformat()))
            self._add(m, start, d.minutes, est.per_minute, heat_price, "history")
        self.currency = cur or self.currency
        self.backfilled = True
        first = dt_util.as_local(datetime.fromtimestamp(rows[0].minute * 60, timezone.utc))
        self.covered_from = (first + timedelta(days=1)).date().isoformat()   # first complete day
        m.request_save()
