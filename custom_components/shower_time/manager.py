"""Runtime glue: live estimate, nightly learning and persistence."""
from __future__ import annotations

from collections.abc import Callable
from dataclasses import asdict
from datetime import datetime, timedelta, timezone
import logging

from homeassistant.components.recorder import get_instance, history, statistics
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import STATE_UNAVAILABLE, STATE_UNKNOWN
from homeassistant.core import CALLBACK_TYPE, Event, HomeAssistant, callback
from homeassistant.helpers.event import (
    async_call_later,
    async_track_state_change_event,
    async_track_time_change,
)
from homeassistant.helpers.storage import Store
from homeassistant.util import dt as dt_util

from . import model
from .const import (
    CONF_BOTTOM,
    CONF_ENERGY,
    CONF_FLOW_SOURCE,
    CONF_HEAD_FLOW,
    CONF_TEMP_MODE,
    CONF_WATER,
    HEAD_PRESETS,
    REF_TEMP,
    WATER_MIN_OBSERVATIONS,
    CONF_HEATING,
    CONF_HEATING_STATES,
    CONF_LOW,
    CONF_PROFILE,
    CONF_SHOWER_TEMP,
    CONF_TOP,
    CONF_VOLUME,
    DEFAULT_HEATING_STATES,
    DEFAULT_LOW,
    DEFAULT_SHOWER_TEMP,
    DEFAULT_VOLUME,
    DOMAIN,
    HISTORY_DAYS,
    LEARN_HOUR,
    LEARN_MINUTE,
    STORAGE_VERSION,
)

_LOGGER = logging.getLogger(__name__)


def _num(state) -> float | None:
    if state is None or state.state in (STATE_UNAVAILABLE, STATE_UNKNOWN, None, ""):
        return None
    try:
        return float(state.state)
    except (TypeError, ValueError):
        return None


def _temp(state) -> float | None:
    """Tank temperature, or None for missing or impossible readings.

    Some heat pumps report -1 or 0 when they have no reading (Weheat does during outages);
    treating those as real temperatures would look like a shower followed by a reheat."""
    v = _num(state)
    return v if v is not None and 0.5 < v < 95 else None


class ShowerTimeManager:
    """One per config entry (one tank)."""

    def __init__(self, hass: HomeAssistant, entry: ConfigEntry) -> None:
        self.hass = hass
        self.entry = entry
        cfg = {**entry.data, **entry.options}
        self.top: str = cfg[CONF_TOP]
        self.bottom: str | None = cfg.get(CONF_BOTTOM) or None
        self.volume: float = float(cfg.get(CONF_VOLUME, DEFAULT_VOLUME))
        # entries created before the auto mode existed had a fixed shower temperature
        self.temp_mode: str = cfg.get(CONF_TEMP_MODE, "fixed" if CONF_SHOWER_TEMP in cfg else "auto")
        self.shower_temp: float = (float(cfg.get(CONF_SHOWER_TEMP, DEFAULT_SHOWER_TEMP)) if self.temp_mode == "fixed"
                                   else REF_TEMP)
        self.flow_source: str = cfg.get(CONF_FLOW_SOURCE, "unknown")
        self.head_flow_manual: float | None = float(cfg[CONF_HEAD_FLOW]) if cfg.get(CONF_HEAD_FLOW) else None
        self.water: str | None = cfg.get(CONF_WATER) or None
        self.heating: str | None = cfg.get(CONF_HEATING) or None
        self.heating_states = {s.strip().lower() for s in str(cfg.get(CONF_HEATING_STATES, DEFAULT_HEATING_STATES)).split(",") if s.strip()}
        self.energy: str | None = cfg.get(CONF_ENERGY) or None
        self.low_minutes: float = float(cfg.get(CONF_LOW, DEFAULT_LOW))
        self.profile: str = cfg.get(CONF_PROFILE, "generic")

        self._store: Store = Store(hass, STORAGE_VERSION, f"{DOMAIN}.{entry.entry_id}")
        self.params: dict = model.new_params(self.profile)
        self.processed_until: int = 0
        self.last_cycle: model.Cycle | None = None
        self.last_learn: str | None = None
        self.last_samples: dict = {}
        self.learning = False

        self.estimate: model.Estimate | None = None
        self.top_value: float | None = None
        self.bottom_value: float | None = None
        self.reheating = False
        # 100 % = the shower minutes right after the last reheat; the fill level drops with use and standing loss
        self.full_ref: float | None = None
        self.full_ref_at: str | None = None
        self._ref_low: float | None = None
        self._last_minutes: float | None = None
        self._listeners: list[Callable[[], None]] = []
        self._unsubs: list[CALLBACK_TYPE] = []

    # ------------------------------------------------------------------ lifecycle
    async def async_start(self) -> None:
        stored = await self._store.async_load()
        if stored and stored.get("profile") == self.profile:
            self.params = model.ensure_params(stored["params"])
            self.processed_until = stored.get("processed_until", 0)
            lc = stored.get("last_cycle")
            self.last_cycle = model.Cycle(**lc) if lc else None
            self.last_learn = stored.get("last_learn")
            self.last_samples = stored.get("last_samples", {})
        if stored:
            self.full_ref = stored.get("full_ref")
            self.full_ref_at = stored.get("full_ref_at")
        watch = [e for e in (self.top, self.bottom, self.heating) if e]   # the water meter is only used for learning
        self._unsubs.append(async_track_state_change_event(self.hass, watch, self._state_changed))
        self._unsubs.append(async_track_time_change(self.hass, self._nightly, hour=LEARN_HOUR, minute=LEARN_MINUTE, second=0))
        if not self.last_learn:                 # first install: learn from what the recorder already has
            self._unsubs.append(async_call_later(self.hass, 120, self._first_learn))
        self.update()
        if self.full_ref is None:               # find the last reheat in the history once
            self.entry.async_create_background_task(self.hass, self._init_full_ref(), f"{DOMAIN}_full_ref")

    async def async_stop(self) -> None:
        for unsub in self._unsubs:
            unsub()
        self._unsubs.clear()

    @callback
    def add_listener(self, cb: Callable[[], None]) -> Callable[[], None]:
        self._listeners.append(cb)
        return lambda: self._listeners.remove(cb)

    # ------------------------------------------------------------------ shower-head flow
    def head_flow(self) -> tuple[float | None, str]:
        """Known shower-head flow (L/min) and where it comes from."""
        if self.flow_source == "manual" and self.head_flow_manual:
            return self.head_flow_manual, "manual"
        if self.flow_source in HEAD_PRESETS:
            return HEAD_PRESETS[self.flow_source], self.flow_source
        if self.flow_source == "water_meter":
            hf = self.params["head_flow"]
            if hf["n"] >= WATER_MIN_OBSERVATIONS:
                return hf["v"], "water_meter"
            return None, "water_meter_learning"
        return None, "unknown"

    # ------------------------------------------------------------------ live estimate
    @callback
    def _state_changed(self, _event: Event) -> None:
        self.update()

    @callback
    def update(self) -> None:
        self.top_value = _temp(self.hass.states.get(self.top))
        self.bottom_value = _temp(self.hass.states.get(self.bottom)) if self.bottom else None
        if self.heating:
            st = self.hass.states.get(self.heating)
            self.reheating = bool(st and str(st.state).lower() in self.heating_states)
        if self.top_value is None:
            self.estimate = None
        else:
            head = self.head_flow()[0] if self.temp_mode == "auto" else None
            self.estimate = model.estimate(self.params, self.top_value, self.bottom_value, volume=self.volume,
                                           shower_temp=self.shower_temp, month=dt_util.now().month, head_flow=head)
            self._track_full(self.estimate.minutes)
        for cb in list(self._listeners):
            cb()

    # ------------------------------------------------------------------ fill level
    def _set_ref(self, minutes: float) -> None:
        self.full_ref = minutes
        self.full_ref_at = dt_util.now().isoformat(timespec="seconds")
        self._ref_low = minutes
        self._store.async_delay_save(self._data, 60)

    @callback
    def _track_full(self, m: float) -> None:
        """Keep the reference at the end of the latest reheat.

        With a reheat entity: follow the minutes while it is on (the last value is the new 100 %).
        Without one: a rise of 3+ minutes above the lowest point since the last reheat is a reheat.
        Sensor values that keep rising just after the reheat state switched off are still followed."""
        prev = self._last_minutes
        self._last_minutes = m
        if self.full_ref is None:
            return                              # still being initialised from the history
        if self._ref_low is None or m < self._ref_low:
            self._ref_low = m
        rising = prev is not None and m > prev
        if (self.heating and self.reheating) or (rising and m >= self._ref_low + 3) or m > self.full_ref:
            self._set_ref(m)

    def fill_percent(self) -> int | None:
        e = self.estimate
        if e is None or not self.full_ref or self.full_ref <= 0:
            return None
        return int(round(max(0.0, min(100.0, 100 * e.minutes / self.full_ref))))

    async def _init_full_ref(self) -> None:
        """First run: the minutes at the end of the last reheat in the past two days."""
        try:
            points = await self.async_history(48)
        except Exception:  # noqa: BLE001
            points = []
        ref = low = prev = ts = None
        for t, v in points:                     # same rules as _track_full, replayed over the history
            if ref is None:
                ref = low = prev = v
                ts = t
                continue
            low = min(low, v)
            if (v > prev and v >= low + 3) or v > ref:
                ref, ts, low = v, t, v
            prev = v
        if ref is None and self.estimate:
            ref = self.estimate.minutes
        if ref is not None:
            self.full_ref = float(ref)
            self.full_ref_at = (dt_util.as_local(datetime.fromtimestamp(ts / 1000, timezone.utc)).isoformat(timespec="seconds")
                                if ts else dt_util.now().isoformat(timespec="seconds"))
            self._ref_low = self.estimate.minutes if self.estimate else ref
            self._store.async_delay_save(self._data, 5)
            self.update()

    # ------------------------------------------------------------------ history for the card
    async def async_history(self, hours: int = 24) -> list[list[float]]:
        """Shower minutes over the last ``hours``, recomputed with the current model from the tank sensors.

        Works from day one: the tank sensors usually have far more history than this integration.
        Returns [[timestamp_ms, minutes], ...] every 5 minutes (only where the value changes).
        """
        now = dt_util.utcnow()
        cached = getattr(self, "_hist_cache", None)
        if cached and cached[0] == hours and (now - cached[1]).total_seconds() < 300:
            return cached[2]
        start = now - timedelta(hours=hours)
        recorder = get_instance(self.hass)
        series = {}
        for ent in (self.top, self.bottom):
            if ent:
                res = await recorder.async_add_executor_job(
                    history.state_changes_during_period, self.hass, start, now, ent, True, False, None, True)
                series[ent] = [(st.last_changed.timestamp(), _temp(st)) for st in res.get(ent, [])]
        head = self.head_flow()[0] if self.temp_mode == "auto" else None

        def compute() -> list[list[float]]:
            out, i, j, top, bot, last = [], 0, 0, None, None, None
            tops, bots = series.get(self.top, []), series.get(self.bottom, []) if self.bottom else []
            t = start.timestamp()
            while t <= now.timestamp():
                while i < len(tops) and tops[i][0] <= t:
                    top = tops[i][1] if tops[i][1] is not None else top
                    i += 1
                while j < len(bots) and bots[j][0] <= t:
                    bot = bots[j][1] if bots[j][1] is not None else bot
                    j += 1
                if top is not None and (not self.bottom or bot is not None):
                    month = dt_util.as_local(datetime.fromtimestamp(t, timezone.utc)).month
                    e = model.estimate(self.params, top, bot, volume=self.volume, shower_temp=self.shower_temp,
                                       month=month, head_flow=head)
                    v = round(e.minutes)
                    if v != last:
                        out.append([int(t * 1000), v])
                        last = v
                t += 300
            if out and out[-1][0] < int(now.timestamp() * 1000):
                out.append([int(now.timestamp() * 1000), last])
            return out

        result = await self.hass.async_add_executor_job(compute)
        self._hist_cache = (hours, now, result)
        return result

    # ------------------------------------------------------------------ learning
    def _data(self) -> dict:
        return {
            "profile": self.profile, "params": self.params, "processed_until": self.processed_until,
            "last_cycle": asdict(self.last_cycle) if self.last_cycle else None, "last_learn": self.last_learn,
            "last_samples": self.last_samples, "full_ref": self.full_ref, "full_ref_at": self.full_ref_at,
        }

    async def _first_learn(self, _now) -> None:
        await self.async_learn()

    async def _nightly(self, _now) -> None:
        await self.async_learn()

    async def async_learn(self) -> None:
        if self.learning:
            return
        self.learning = True
        try:
            await self._learn()
        except Exception:  # noqa: BLE001 - learning must never break the integration
            _LOGGER.exception("Learning failed; keeping the current model")
        finally:
            self.learning = False
            self.update()

    async def _learn(self) -> None:
        now = dt_util.utcnow()
        end = now - timedelta(minutes=30)
        start = now - timedelta(days=HISTORY_DAYS)
        if self.processed_until:
            start = max(start, datetime.fromtimestamp(self.processed_until * 60, timezone.utc) - timedelta(hours=6))
        recorder = get_instance(self.hass)
        series: dict[str, list] = {}
        for ent in (self.top, self.bottom, self.heating, self.water if self.flow_source == "water_meter" else None):
            if not ent:
                continue
            res = await recorder.async_add_executor_job(
                history.state_changes_during_period, self.hass, start, end, ent, True, False, None, True)
            series[ent] = res.get(ent, [])
        rows = await self.hass.async_add_executor_job(self._rows, series, start, end)
        if len(rows) < 600:
            _LOGGER.info("Not enough history to learn yet (%d minutes)", len(rows))
            return
        energy = None
        if self.energy:
            stats = await recorder.async_add_executor_job(
                statistics.statistics_during_period, self.hass, start, end, {self.energy}, "hour",
                {"energy": "kWh"}, {"change"})
            energy = {}
            for row in stats.get(self.energy, []):
                ts = row["start"]
                ts = ts.timestamp() if isinstance(ts, datetime) else float(ts)
                energy[int(ts // 60)] = float(row.get("change") or 0.0)

        water = self._water_series(series.get(self.water, [])) if self.water and self.flow_source == "water_meter" else None

        def month_of(minute: int) -> int:
            return dt_util.as_local(datetime.fromtimestamp(minute * 60, timezone.utc)).month

        result, last = await self.hass.async_add_executor_job(
            lambda: model.learn(self.params, rows, volume=self.volume, shower_temp=self.shower_temp,
                                since=self.processed_until, energy_by_hour=energy,
                                previous_cycle=self.last_cycle, month_of=month_of, water=water))
        self.params = result.params
        self.last_cycle = last
        self.processed_until = int(end.timestamp() // 60)
        self.last_learn = dt_util.now().isoformat(timespec="seconds")
        self.last_samples = result.samples
        await self._store.async_save(self._data())
        _LOGGER.debug("Learned from %d cycles / %d draws: %s", result.cycles, result.draws, result.samples)

    def _water_series(self, states: list) -> tuple[str, list[tuple[int, float]]] | None:
        """Water meter history as ("rate", L/min) or ("total", litres), whatever the meter reports."""
        st = self.hass.states.get(self.water)
        unit = str((st.attributes.get("unit_of_measurement") if st else "") or "").replace(" ", "").lower()
        rates = {"l/min": 1.0, "l/h": 1 / 60, "m³/h": 1000 / 60, "m3/h": 1000 / 60, "gal/min": 3.785}
        totals = {"l": 1.0, "m³": 1000.0, "m3": 1000.0, "gal": 3.785}
        if unit in rates:
            kind, factor = "rate", rates[unit]
        elif unit in totals:
            kind, factor = "total", totals[unit]
        else:
            _LOGGER.warning("Water meter %s has unit %r; expected a flow (L/min) or a volume (L, m³)", self.water, unit)
            return None
        out = []
        for s in states:
            v = _num(s)
            if v is not None:
                out.append((int(s.last_changed.timestamp() // 60), v * factor))
        return kind, out

    def _rows(self, series: dict[str, list], start: datetime, end: datetime) -> list[model.Row]:
        """Forward-fill all series onto a one-minute grid."""
        def prep(ent, conv):
            out = []
            for st in series.get(ent, []):
                out.append((int(st.last_changed.timestamp() // 60), conv(st)))
            return out
        top = [x for x in prep(self.top, _temp) if x[1] is not None]           # skip invalid readings: hold the last good one
        bottom = [x for x in prep(self.bottom, _temp) if x[1] is not None] if self.bottom else []
        heat = prep(self.heating, lambda s: str(s.state).lower() in self.heating_states) if self.heating else []
        idx = {"t": 0, "b": 0, "h": 0}
        cur = {"t": None, "b": None, "h": False}
        rows: list[model.Row] = []
        for m in range(int(start.timestamp() // 60), int(end.timestamp() // 60)):
            for key, seq in (("t", top), ("b", bottom), ("h", heat)):
                while idx[key] < len(seq) and seq[idx[key]][0] <= m:
                    cur[key] = seq[idx[key]][1]
                    idx[key] += 1
            if cur["t"] is None or (self.bottom and cur["b"] is None):
                continue
            rows.append(model.Row(minute=m, top=cur["t"], bottom=cur["b"] if self.bottom else None, heating=bool(cur["h"])))
        return rows
