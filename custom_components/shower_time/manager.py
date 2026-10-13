"""Runtime glue: live estimate, nightly learning and persistence."""
from __future__ import annotations

from collections.abc import Callable
from dataclasses import asdict, replace
from datetime import date, datetime, timedelta, timezone
import logging
import math

from homeassistant.components.recorder import get_instance, history, statistics
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import STATE_UNAVAILABLE, STATE_UNKNOWN
from homeassistant.core import CALLBACK_TYPE, Event, HomeAssistant, callback
from homeassistant.helpers import issue_registry as ir
from homeassistant.helpers.event import (
    async_call_later,
    async_track_state_change_event,
    async_track_time_change,
    async_track_time_interval,
)
from homeassistant.helpers.storage import Store
from homeassistant.util import dt as dt_util

from . import insights, model
from .ledger import ShowerLedger
from .const import (
    CONF_BATH,
    CONF_BOTTOM,
    CONF_COP,
    CONF_ELEC,
    CONF_ENERGY,
    CONF_PRICE,
    DEFAULT_COP,
    MAX_SHOWER_MINUTES,
    MIN_SHOWER_MINUTES,
    CONF_FLOW_SOURCE,
    CONF_HEAD_FLOW,
    CONF_OUTDOOR,
    COLD_TAU_DAYS,
    CONF_TEMP_MODE,
    CONF_WATER,
    HEAD_PRESETS,
    REF_TEMP,
    WATER_MIN_OBSERVATIONS,
    CONF_HEATING,
    CONF_HEATING_STATES,
    CONF_LEGIONELLA_DAYS,
    CONF_LEGIONELLA_TEMP,
    CONF_LOW,
    CONF_PROFILE,
    CONF_SHOWER_TEMP,
    CONF_TOP,
    CONF_VOLUME,
    DEFAULT_HEATING_STATES,
    DEFAULT_LEGIONELLA_DAYS,
    DEFAULT_LEGIONELLA_TEMP,
    DEFAULT_LOW,
    DEFAULT_SHOWER_TEMP,
    DEFAULT_VOLUME,
    DOMAIN,
    HISTORY_DAYS,
    LEARN_HOUR,
    LEARN_MINUTE,
    LOG_DAYS,
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


def _temp(state, fahrenheit: bool = False) -> float | None:
    """Tank temperature in °C, or None for missing or impossible readings.

    Some heat pumps report -1 or 0 when they have no reading (Weheat does during outages);
    treating those as real temperatures would look like a shower followed by a reheat.
    History states are read without attributes, so the unit is passed in from the live entity."""
    v = _num(state)
    if v is None:
        return None
    if fahrenheit:
        v = (v - 32) * 5 / 9
    return v if 0.5 < v < 95 else None


class ShowerTimeManager:
    """One per config entry (one tank). Works in °C internally; °F sensors are converted on the way in."""

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
        self.outdoor: str | None = cfg.get(CONF_OUTDOOR) or None
        self.price: str | None = cfg.get(CONF_PRICE) or None
        self.elec: str | None = cfg.get(CONF_ELEC) or None
        self.cop_manual: float = float(cfg.get(CONF_COP) or DEFAULT_COP)
        self.ledger = ShowerLedger()
        self._ema_by_day: dict[str, float] = {}       # local date -> outdoor running average
        self.ema_now: float | None = None
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
        self.full_ref: float | None = None          # minutes at the last reheat (kept for display/compat)
        self.full_ref_avg: float | None = None      # tank average temperature right after the last reheat
        self.full_ref_at: str | None = None
        self._ref_low: float | None = None
        self._last_minutes: float | None = None
        # live countdown while someone showers (sensors only report every few minutes)
        self.showering = False
        self.live_offset = 0.0                      # minutes counted down since the last real reading
        self._sensor_at = None                      # utc time of the last top/bottom reading
        self._last_fall_at = None                   # utc time of the last reading that showed hot water use
        self._prev = {}                             # entity -> (value, utc time)
        self._listeners: list[Callable[[], None]] = []
        self._event_listeners: list[Callable[[str, dict], None]] = []
        self._unsubs: list[CALLBACK_TYPE] = []
        # smart extras (insights.py): habits and today's forecast, heat loss and leaks, legionella, health
        self.habits: dict = insights.new_habits()
        self.loss_samples: list[dict] = []
        self.drip_nights: list[dict] = []
        self.loss_backfilled = False                # the full history was searched once for heat-loss measurements
        self.loss: dict = self._loss_status()
        self.legionella_temp = float(cfg.get(CONF_LEGIONELLA_TEMP, DEFAULT_LEGIONELLA_TEMP))
        self.legionella_days = float(cfg.get(CONF_LEGIONELLA_DAYS, DEFAULT_LEGIONELLA_DAYS))
        self.legionella_at: str | None = None
        self.legionella_checked = False             # the history was searched (so "never" means never)
        self.learn_failures = 0
        self._missing_since: dict[str, datetime] = {}
        self._swap_since: datetime | None = None
        # what kind of hot water use: followed live, confirmed by heat or a bathroom signal (insights.UseSession)
        self.bath: str | None = cfg.get(CONF_BATH) or None
        self.bath_signal: insights.BathSignal | None = None
        self.use: insights.UseSession | None = None
        self.use_stats: dict[str, list[float]] = {"shower": [], "tap": []}
        self.coil_events = 0                        # heat that left without water (reheat start, coil)

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
            self.habits = stored.get("habits") or self.habits
            self.loss_samples = stored.get("loss_samples") or []
            self.drip_nights = stored.get("drip_nights") or []
            self.loss_backfilled = stored.get("loss_backfilled", False)
            self.loss = self._loss_status()
            self.legionella_at = stored.get("legionella_at")
            self.learn_failures = stored.get("learn_failures", 0)
            self.use_stats = stored.get("use_stats") or self.use_stats
            self.ledger.load(stored.get("ledger"))
            self.full_ref_avg = stored.get("full_ref_avg")
            self.full_ref_at = stored.get("full_ref_at")
            # a reference in minutes only (before 0.5.1) depends on the model it was computed with: rebuild it
            self.full_ref = stored.get("full_ref") if self.full_ref_avg is not None else None
        watch = [e for e in (self.top, self.bottom, self.heating, self.bath) if e]   # the water meter is only used for learning
        if self.bath:
            self.bath_signal = insights.BathSignal(self._bath_kind())
            self.entry.async_create_background_task(self.hass, self._seed_bath(), f"{DOMAIN}_bath")
        self._unsubs.append(async_track_state_change_event(self.hass, watch, self._state_changed))
        self._unsubs.append(async_track_time_change(self.hass, self._nightly, hour=LEARN_HOUR, minute=LEARN_MINUTE, second=0))
        self._unsubs.append(async_track_time_interval(self.hass, self._tick, timedelta(seconds=15)))
        self._unsubs.append(async_track_time_change(self.hass, self._midnight, hour=0, minute=0, second=5))
        self._unsubs.append(async_track_time_interval(self.hass, self._check_health, timedelta(minutes=10)))
        self.entry.async_create_background_task(self.hass, self._start_ledger(), f"{DOMAIN}_ledger")
        if not self.last_learn:                 # first install: learn from what the recorder already has
            self._unsubs.append(async_call_later(self.hass, 120, self._first_learn))
        if self.outdoor:
            self.entry.async_create_background_task(self.hass, self._refresh_ema(update=True), f"{DOMAIN}_ema")
        self.update()
        if self.full_ref is None:               # find the last reheat in the history once
            self.entry.async_create_background_task(self.hass, self._init_full_ref(), f"{DOMAIN}_full_ref")
        if self.legionella_at is None:          # the last legionella-proof heat-up, from the statistics
            self.entry.async_create_background_task(self.hass, self._init_legionella(), f"{DOMAIN}_legionella")
        else:
            self.legionella_checked = True
        self._sync_loss_issues()

    async def async_stop(self) -> None:
        for unsub in self._unsubs:
            unsub()
        self._unsubs.clear()

    @callback
    def add_listener(self, cb: Callable[[], None]) -> Callable[[], None]:
        self._listeners.append(cb)
        return lambda: self._listeners.remove(cb)

    @callback
    def add_event_listener(self, cb: Callable[[str, dict], None]) -> Callable[[], None]:
        """Shower started / ended, for the event entity."""
        self._event_listeners.append(cb)
        return lambda: self._event_listeners.remove(cb)

    @callback
    def _fire(self, kind: str, data: dict) -> None:
        for cb in list(self._event_listeners):
            cb(kind, data)

    def _is_f(self, entity: str | None) -> bool:
        st = self.hass.states.get(entity) if entity else None
        unit = str((st.attributes.get("unit_of_measurement") if st else "") or "").replace(" ", "")
        return unit in ("°F", "F", "℉")

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
        self.top_value = _temp(self.hass.states.get(self.top), self._is_f(self.top))
        self.bottom_value = _temp(self.hass.states.get(self.bottom), self._is_f(self.bottom)) if self.bottom else None
        if self.heating:
            st = self.hass.states.get(self.heating)
            self.reheating = bool(st and str(st.state).lower() in self.heating_states)
        self._feed_bath()
        self._detect_use()
        self._track_legionella()
        if self.top_value is None:
            self.estimate = None
        else:
            head = self.head_flow()[0] if self.temp_mode == "auto" else None
            now = dt_util.now()
            self.estimate = model.estimate(self.params, self.top_value, self.bottom_value, volume=self.volume,
                                           shower_temp=self.shower_temp, month=now.month, head_flow=head,
                                           doy=now.timetuple().tm_yday, ema=self.ema_now)
            self._track_full(self.estimate.minutes)
            self.ledger.on_estimate(self, self.estimate)
            self._classify_use()
        for cb in list(self._listeners):
            cb()

    # ------------------------------------------------------------------ live countdown
    LIVE_MAX_MINUTES = 4.0          # never count down further than this without a new reading
    USE_TIMEOUT = timedelta(minutes=5)

    @callback
    def _detect_use(self) -> None:
        """A new tank reading that falls fast (cold water coming in) while not reheating = hot water in use."""
        now = dt_util.utcnow()
        changed, falling, fall_from = False, False, None
        before = {ent: v[0] for ent, v in self._prev.items()}     # readings before this update
        for ent, val in ((self.top, self.top_value), (self.bottom, self.bottom_value)):
            if not ent or val is None:
                continue
            prev = self._prev.get(ent)
            if prev is None or prev[0] != val:
                changed = True
                if prev is not None:
                    minutes = max((now - prev[1]).total_seconds() / 60, 0.5)
                    if (prev[0] - val) / minutes >= 0.3:
                        falling = True
                        fall_from = prev[1] if fall_from is None else min(fall_from, prev[1])
                self._prev[ent] = (val, now)
        if not changed:
            return
        self._sensor_at, self.live_offset = now, 0.0       # a real reading: start from the sensors again
        if falling and not self.reheating:
            if not self.showering:
                self.ledger.session_start = fall_from or now        # the use began after the previous reading
                self.use = insights.UseSession(
                    start=self.ledger.session_start.timestamp() / 60,
                    start_avg=self.estimate.average if self.estimate else None,   # still the previous estimate
                    start_top=before.get(self.top), start_bottom=before.get(self.bottom) if self.bottom else None)
            self.showering, self._last_fall_at = True, now
        elif self.showering and (self.reheating or self._last_fall_at is None or now - self._last_fall_at > self.USE_TIMEOUT):
            self._end_shower()

    @callback
    def _tick(self, _now) -> None:
        if not self.showering or self.estimate is None:
            return
        now = dt_util.utcnow()
        if self.reheating or (self._last_fall_at and now - self._last_fall_at > self.USE_TIMEOUT):
            self._end_shower()
            self.live_offset = 0.0
        elif self._sensor_at is not None:
            self.live_offset = min(self.LIVE_MAX_MINUTES, (now - self._sensor_at).total_seconds() / 60)
        for cb in list(self._listeners):
            cb()

    @callback
    def _end_shower(self) -> None:
        """A hot water use ended: log it as a shower or as other hot water use (or ignore it: no water)."""
        self.showering = False
        use, start = self.use, self.ledger.session_start
        self.use = None
        if start and self._last_fall_at:
            minutes = (self._last_fall_at - start).total_seconds() / 60
            kind = use.final(minutes, MAX_SHOWER_MINUTES) if use else "tap"
            if kind == "shower":
                entry = self.ledger.shower_ended(self, start, self._last_fall_at, self.estimate, min_minutes=2.0)
                if entry is not None:
                    self._remember_use("shower", use.drawn)
                    self._fire("shower_ended", {**entry, "currency": self.ledger.currency or None})
            elif kind == "coil":
                self.coil_events += 1
            elif use is not None and use.drawn >= 0.15:   # below that nothing measurable left the tank
                if kind == "tap":
                    self._remember_use("tap", use.drawn)
                entry = self.ledger.other_ended(self, kind, start, minutes, use.drawn * self.volume * model.CP)
                self._fire("hot_water_used", {**entry, "currency": self.ledger.currency or None})
        self.ledger.session_start = None

    # ------------------------------------------------------------------ what kind of hot water use
    def _bath_kind(self) -> str:
        domain = self.bath.split(".")[0]
        if domain != "sensor":
            return "on_off"
        st = self.hass.states.get(self.bath)
        dc = st.attributes.get("device_class") if st else None
        unit = str(st.attributes.get("unit_of_measurement") or "") if st else ""
        return "humidity" if dc == "humidity" or unit == "%" else "power"

    def _bath_value(self, st):
        if st is None or st.state in (STATE_UNAVAILABLE, STATE_UNKNOWN):
            return None
        if self.bath_signal and self.bath_signal.kind == "on_off":
            return str(st.state).lower() in ("on", "open", "detected", "home", "playing")
        return _num(st)

    @callback
    def _feed_bath(self) -> None:
        if self.bath_signal is not None:
            self.bath_signal.feed(dt_util.utcnow().timestamp() / 60, self._bath_value(self.hass.states.get(self.bath)))

    async def _seed_bath(self) -> None:
        """The usual power of a bathroom fan is learned from the last day of its history."""
        if self.bath_signal is None or self.bath_signal.kind != "power":
            return
        try:
            end = dt_util.utcnow()
            res = await get_instance(self.hass).async_add_executor_job(
                history.state_changes_during_period, self.hass, end - timedelta(hours=24), end, self.bath, True, False, None, True)
            for st in res.get(self.bath, []):
                v = _num(st)
                if v is not None:
                    self.bath_signal.feed(st.last_changed.timestamp() / 60, v)
            self.bath_signal.active_at = None       # the past day does not count as "in use now"
        except Exception:  # noqa: BLE001
            _LOGGER.debug("No history for the bathroom signal yet")

    def bath_active(self) -> bool:
        return bool(self.bath_signal and self.bath_signal.active(dt_util.utcnow().timestamp() / 60))

    def confirm_k(self) -> float:
        return insights.learn_confirm_k(self.use_stats["shower"], self.use_stats["tap"])

    def _remember_use(self, kind: str, drawn: float) -> None:
        self.use_stats[kind] = (self.use_stats[kind] + [round(drawn, 2)])[-50:]
        self.request_save()

    def use_kind(self) -> str | None:
        """What the hot water use going on now looks like: unknown (just started, or a tap), shower, fill or
        coil (heat leaving without water)."""
        return self.use.kind if self.showering and self.use else None

    @callback
    def _classify_use(self) -> None:
        if not self.showering or self.use is None or self.estimate is None:
            return
        if self.use.update(dt_util.utcnow().timestamp() / 60, self.estimate.average, self.bath_active(),
                           self.confirm_k(), top=self.top_value, bottom=self.bottom_value, cold=self.estimate.cold):
            self._fire("shower_started", {"started": dt_util.as_local(self.ledger.session_start).isoformat(timespec="seconds"),
                                          "confirmed_after_minutes": self.use.confirmed_after,
                                          "bathroom_signal": self.use.bath_seen})

    @callback
    def _midnight(self, _now) -> None:
        self._update_habits()
        for cb in list(self._listeners):              # "today" counters start again
            cb()

    # ------------------------------------------------------------------ shower habits and today's forecast
    @callback
    def _update_habits(self) -> None:
        """Add every completed day the shower log fully covers to the habit profile."""
        cov = self.ledger.covered_from
        if not cov:
            return
        today = dt_util.now().date()
        day = max(date.fromisoformat(cov), today - timedelta(days=LOG_DAYS - 1))
        if self.habits.get("last_day"):
            day = max(day, date.fromisoformat(self.habits["last_day"]) + timedelta(days=1))
        by_day: dict[date, list[tuple[float, float]]] = {}
        for e in self.ledger.log:
            t = dt_util.as_local(datetime.fromisoformat(e["start"]))
            by_day.setdefault(t.date(), []).append((t.hour + t.minute / 60, e["minutes"]))
        changed = False
        while day < today:
            insights.add_day(self.habits, day.weekday(), day.isoformat(), insights.minutes_by_hour(by_day.get(day, [])))
            day += timedelta(days=1)
            changed = True
        if changed:
            self.request_save()

    def forecast(self) -> insights.Forecast | None:
        """Will the hot water last today (without a reheat in between)? None until a week of habits is known."""
        e = self.current
        if e is None or not self.bottom or not insights.habits_ready(self.habits):
            return None                             # without a bottom sensor showers are not seen
        minutes = e.minutes
        if self.reheating:                          # being filled: count on what it holds when full
            minutes = max(minutes, self.ref_minutes() or 0.0)
        now = dt_util.now()
        return insights.forecast(self.habits, weekday=now.weekday(), hour=now.hour + now.minute / 60,
                                 minutes_now=minutes, per_minute=e.per_minute, loss_k_h=self.params["loss"]["v"],
                                 typical=self.params["duration"]["v"])

    def forecast_attrs(self) -> dict | None:
        """Today's forecast with clock times instead of hours (for attributes and notifications)."""
        f = self.forecast()
        if f is None:
            return None
        now = dt_util.now()
        day = now.replace(hour=0, minute=0, second=0, microsecond=0)
        return {"enough": f.enough, "expected_minutes": f.expected_minutes, "available_minutes": f.available_minutes,
                "runs_out_at": (day + timedelta(hours=f.runs_out_at)).isoformat(timespec="minutes")
                if f.runs_out_at is not None else None,
                "next_shower_at": (now + timedelta(hours=f.next_shower_at)).replace(second=0, microsecond=0)
                .isoformat(timespec="minutes") if f.next_shower_at is not None else None}

    # ------------------------------------------------------------------ legionella
    @callback
    def _track_legionella(self) -> None:
        t, b = self.top_value, self.bottom_value
        if t is not None and t >= self.legionella_temp and (self.bottom is None or (b is not None and b >= self.legionella_temp)):
            stamp = dt_util.now().isoformat(timespec="minutes")
            if stamp != self.legionella_at:
                self.legionella_at = stamp
                self.legionella_checked = True
                self.request_save()

    async def _init_legionella(self) -> None:
        """The last hour in which the whole tank (top and bottom) reached the legionella temperature."""
        try:
            end = dt_util.utcnow()
            ids = {self.top} | ({self.bottom} if self.bottom else set())
            stats = await get_instance(self.hass).async_add_executor_job(
                statistics.statistics_during_period, self.hass, end - timedelta(days=45), end, ids, "hour",
                {"temperature": "°C"}, {"max"})

            def by_hour(ent: str) -> dict[float, float]:
                out = {}
                for r in stats.get(ent, []):
                    ts = r["start"]
                    if r.get("max") is not None:
                        out[ts.timestamp() if isinstance(ts, datetime) else float(ts)] = float(r["max"])
                return out
            top = by_hour(self.top)
            bot = by_hour(self.bottom) if self.bottom else None
            if not top:
                return                              # no long-term statistics: wait for the live readings
            hits = [t for t, v in top.items() if v >= self.legionella_temp
                    and (bot is None or bot.get(t, -99.0) >= self.legionella_temp)]
            if hits and self.legionella_at is None:
                self.legionella_at = dt_util.as_local(datetime.fromtimestamp(max(hits) + 3599, timezone.utc)).isoformat(timespec="minutes")
                self.request_save()
            self.legionella_checked = True
        except Exception:  # noqa: BLE001
            _LOGGER.exception("Could not search the history for the last legionella heat-up")
        for cb in list(self._listeners):
            cb()

    def legionella_overdue(self) -> bool | None:
        if not self.legionella_checked:
            return None
        if self.legionella_at is None:
            return True                             # not once in the 45 days of statistics
        age = dt_util.now() - datetime.fromisoformat(self.legionella_at)
        return age > timedelta(days=self.legionella_days)

    # ------------------------------------------------------------------ health (Home Assistant repairs)
    def _issue_id(self, key: str) -> str:
        return f"{self.entry.entry_id}_{key.replace(':', '_')}"

    @callback
    def _issue(self, key: str, active: bool, placeholders: dict | None = None) -> None:
        if active:
            ir.async_create_issue(self.hass, DOMAIN, self._issue_id(key), is_fixable=False,
                                  severity=ir.IssueSeverity.WARNING, translation_key=key.split(":")[0],
                                  translation_placeholders={"name": self.entry.title, **(placeholders or {})})
        else:
            ir.async_delete_issue(self.hass, DOMAIN, self._issue_id(key))

    @callback
    def _check_health(self, _now=None) -> None:
        now = dt_util.utcnow()
        for key, ent, val in (("top", self.top, self.top_value), ("bottom", self.bottom, self.bottom_value)):
            if not ent:
                continue
            if val is None:
                since = self._missing_since.setdefault(key, now)
                self._issue(f"sensor_missing:{key}", now - since >= timedelta(hours=2), {"entity": ent})
            else:
                self._missing_since.pop(key, None)
                self._issue(f"sensor_missing:{key}", False)
        t, b = self.top_value, self.bottom_value
        swapped = t is not None and b is not None and b > t + 5 and not self.reheating
        self._swap_since = (self._swap_since or now) if swapped else None
        self._issue("sensors_swapped", bool(self._swap_since and now - self._swap_since >= timedelta(hours=6)),
                    {"top": self.top, "bottom": self.bottom or ""})
        self._issue("learning_failed", self.learn_failures >= 3, {"nights": str(self.learn_failures)})

    def _loss_status(self) -> dict:
        st = insights.loss_status(self.loss_samples)
        st["dripping"] = insights.drip_status(self.drip_nights)
        st["leak_like"] = bool(st["leak_like"] or st["dripping"])
        return st

    @callback
    def _sync_loss_issues(self) -> None:
        st = self.loss
        ph = {"baseline": f"{st['ua_baseline']:.2f}" if st.get("ua_baseline") else "-",
              "recent": f"{st['ua_recent']:.2f}" if st.get("ua_recent") else "-",
              "ratio": f"{st['ratio']:.1f}" if st.get("ratio") else "-"}
        self._issue("possible_leak", bool(st.get("leak_like")), ph)
        self._issue("heat_loss_raised", bool(st.get("raised")) and not st.get("leak_like"), ph)

    def heat_loss_now(self) -> float | None:
        """Watts the tank loses right now, from its measured heat-loss coefficient."""
        if self.top_value is None:
            return None
        mean = (self.top_value + self.bottom_value) / 2 if self.bottom_value is not None else self.top_value
        return insights.heat_loss_watts(self.loss.get("ua_recent"), mean)

    @callback
    def request_save(self) -> None:
        self._store.async_delay_save(self._data, 30)

    async def _start_ledger(self) -> None:
        await self.ledger.async_refresh_cop(self)
        if self.ledger.needs_reprice(self) or any(
                not MIN_SHOWER_MINUTES <= e["minutes"] <= MAX_SHOWER_MINUTES for e in self.ledger.log):
            self.ledger.reset_history()          # price added later, or entries from before the shower limits
        if not self.ledger.backfilled:
            end = dt_util.utcnow()
            try:
                rows = await self._fetch_rows(end - timedelta(days=8), end)
                await self.ledger.async_backfill(self, rows)
            except Exception:  # noqa: BLE001
                _LOGGER.exception("Could not fill the shower log from the history")
        self._update_habits()
        self.update()

    @property
    def current(self) -> model.Estimate | None:
        """The estimate as shown: the last real one minus the live countdown while showering."""
        e = self.estimate
        if e is None or self.live_offset <= 0:
            return e
        m = max(0.0, e.minutes - self.live_offset)
        return replace(e, minutes=m, liters=m * e.flow, showers=int(m // self.params["duration"]["v"]))

    # ------------------------------------------------------------------ outdoor running average (cold water)
    def _ema_on(self, day: str) -> float | None:
        if not self._ema_by_day:
            return None
        if day in self._ema_by_day:
            return self._ema_by_day[day]
        earlier = [d for d in self._ema_by_day if d <= day]
        return self._ema_by_day[max(earlier)] if earlier else None

    async def _refresh_ema(self, update: bool = False) -> None:
        """~3-week exponential average of the daily mean outdoor temperature, from long-term statistics."""
        try:
            end = dt_util.utcnow()
            start = end - timedelta(days=70)
            stats = await get_instance(self.hass).async_add_executor_job(
                statistics.statistics_during_period, self.hass, start, end, {self.outdoor}, "hour",
                {"temperature": "°C"}, {"mean"})
            days: dict[str, list[float]] = {}
            for row in stats.get(self.outdoor, []):
                if row.get("mean") is None:
                    continue
                ts = row["start"]
                ts = ts.timestamp() if isinstance(ts, datetime) else float(ts)
                day = dt_util.as_local(datetime.fromtimestamp(ts, timezone.utc)).date().isoformat()
                days.setdefault(day, []).append(float(row["mean"]))
            if len(days) < 14:                  # too little history yet: keep using the monthly table
                self._ema_by_day, self.ema_now = {}, None
                return
            alpha = 1 - math.exp(-1 / COLD_TAU_DAYS)
            ema, out = None, {}
            for day in sorted(days):
                mean = sum(days[day]) / len(days[day])
                ema = mean if ema is None else ema + alpha * (mean - ema)
                out[day] = round(ema, 2)
            self._ema_by_day = out
            self.ema_now = out[max(out)]
        except Exception:  # noqa: BLE001 - without it we fall back to the monthly table
            _LOGGER.exception("Could not read the outdoor temperature statistics")
            self._ema_by_day, self.ema_now = {}, None
        if update:
            self.update()

    def cold_source(self) -> str:
        return "outdoor" if self.ema_now is not None else "table"

    # ------------------------------------------------------------------ fill level
    def _set_ref(self, minutes: float) -> None:
        self.full_ref = minutes
        if self.estimate is not None:
            self.full_ref_avg = self.estimate.average
        self.full_ref_at = dt_util.now().isoformat(timespec="seconds")
        self._ref_low = minutes
        self._store.async_delay_save(self._data, 60)

    @callback
    def _track_full(self, m: float) -> None:
        """Keep 100 % at the end of the latest *complete* reheat.

        A reheat that leaves the tank evenly hot (top and bottom within 2.5 °C) sets a new 100 %, also when it
        heats to a lower temperature than last time (smart controllers vary the target). A partial top-up only
        raises the fill level; it never becomes the new 100 % unless it beats the old one.
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
        if self.heating:
            heating = self.reheating
        else:
            rising = prev is not None and m > prev + 0.2
            if rising and m >= self._ref_low + 3:
                self._rise_at = now
            heating = getattr(self, "_rise_at", None) is not None and (now - self._rise_at).total_seconds() < 1200
        was = getattr(self, "_was_heating", False)
        self._was_heating = heating
        if was and not heating:                 # a reheat just finished
            even = self.bottom_value is None or (self.top_value is not None and self.top_value - self.bottom_value <= 2.5)
            if even or m > ref_now:
                self._set_ref(m)
            self._rise_at = None
            return
        if not heating and m > ref_now + 0.3:   # sensors still settling after the reheat state switched off
            self._set_ref(m)

    def ref_minutes(self) -> float | None:
        """Minutes the tank held right after the last reheat, with the *current* model (so the fill level does
        not jump when the model learns or the cold water estimate changes)."""
        e = self.estimate
        if e is None:
            return self.full_ref
        if self.full_ref_avg is not None:
            return max(0.0, (self.full_ref_avg - e.empty_at) / e.per_minute)
        return self.full_ref

    def since_reheat(self) -> dict | None:
        """Where the hot water went since the last complete reheat: left, showered and lost to cooling.

        Showered comes from the showers actually logged since the reheat (one shower minute takes about one
        minute off the estimate); the rest of the drop is standing loss. The three parts always add up to the
        minutes the tank held right after the reheat."""
        e, ref = self.current, self.ref_minutes()
        if e is None or not ref or not self.full_ref_at:
            return None
        try:
            since = dt_util.parse_datetime(self.full_ref_at)
        except (TypeError, ValueError):
            return None
        if since is None:
            return None
        hours = max(0.0, (dt_util.now() - since).total_seconds() / 3600)
        # showered = the showers actually logged since the reheat (plus a shower still running); the rest of the
        # drop is cooling. One minute of showering takes about one minute off the estimate.
        key = dt_util.as_local(since).isoformat(timespec="minutes")
        logged = sum(x["minutes"] for x in self.ledger.log if x["start"] >= key)
        if self.showering and self.ledger.session_start and self.use_kind() != "coil":
            logged += max(0.0, (dt_util.utcnow() - self.ledger.session_start).total_seconds() / 60)
        # other hot water (washing up, taps) is use too, not cooling: its heat in shower minutes
        per_min_kwh = e.per_minute * self.volume * model.CP
        if per_min_kwh > 0:
            logged += sum(x["heat_kwh"] for x in self.ledger.other if x["start"] >= key) / per_min_kwh
        drop = max(0.0, ref - e.minutes)
        used = min(drop, logged)
        cooling = drop - used
        heat = cooling * e.per_minute * self.volume * model.CP
        price = self.ledger.tank_price
        if price is None:
            p, _ = self.ledger.price_now(self)
            price = p / self.ledger.cop(self) if p is not None else None
        return {"since": self.full_ref_at, "hours": round(hours, 1), "reference_minutes": round(ref, 1),
                "remaining_minutes": round(e.minutes, 1), "showered_minutes": round(used, 1),
                "cooling_minutes": round(cooling, 1), "cooling_kwh_heat": round(heat, 2),
                "cooling_cost": round(heat * price, 2) if price is not None else None}

    def standing_loss_per_day(self) -> dict:
        """What standing loss costs per day (heat, electricity, money) with the learned loss rate."""
        heat = self.params["loss"]["v"] * 24 * self.volume * model.CP
        elec = heat / self.ledger.cop(self)
        price = self.ledger.tank_price
        if price is None:
            p, _ = self.ledger.price_now(self)
            price = p / self.ledger.cop(self) if p is not None else None
        return {"kwh_heat": round(heat, 2), "kwh_electricity": round(elec, 2),
                "cost": round(heat * price, 2) if price is not None else None,
                "cost_year": round(heat * price * 365) if price is not None else None}

    def fill_percent(self) -> int | None:
        e, ref = self.current, self.ref_minutes()
        if e is None or not ref or ref <= 0:
            return None
        return int(round(max(0.0, min(100.0, 100 * e.minutes / ref))))

    async def _init_full_ref(self) -> None:
        """First run: the minutes at the end of the last reheat in the past two days."""
        try:
            points = await self.async_history(48, with_average=True)
        except Exception:  # noqa: BLE001
            points = []
        ref = low = prev = ts = avg = None
        for t, v, a in points:                  # same rules as _track_full, replayed over the history
            if ref is None:
                ref = low = prev = v
                ts, avg = t, a
                continue
            low = min(low, v)
            if (v > prev and v >= low + 3) or v > ref:
                ref, ts, low, avg = v, t, v, a
            prev = v
        if ref is None and self.estimate:
            ref = self.estimate.minutes
        if ref is not None:
            self.full_ref = float(ref)
            self.full_ref_avg = avg if avg is not None else (
                self.estimate.empty_at + float(ref) * self.estimate.per_minute if self.estimate else None)
            self.full_ref_at = (dt_util.as_local(datetime.fromtimestamp(ts / 1000, timezone.utc)).isoformat(timespec="seconds")
                                if ts else dt_util.now().isoformat(timespec="seconds"))
            self._ref_low = self.estimate.minutes if self.estimate else ref
            self._store.async_delay_save(self._data, 5)
            self.update()

    # ------------------------------------------------------------------ history for the card
    async def async_history(self, hours: int = 24, with_average: bool = False) -> list[list[float]]:
        """Shower minutes over the last ``hours``, recomputed with the current model from the tank sensors.

        Works from day one: the tank sensors usually have far more history than this integration.
        Returns [[timestamp_ms, minutes], ...] every 5 minutes (only where the value changes).
        """
        now = dt_util.utcnow()
        cached = getattr(self, "_hist_cache", None)
        if cached and cached[0] == hours and (now - cached[1]).total_seconds() < 300:
            return cached[2] if with_average else [[t, v] for t, v, _ in cached[2]]
        start = now - timedelta(hours=hours)
        recorder = get_instance(self.hass)
        series = {}
        for ent in (self.top, self.bottom):
            if ent:
                res = await recorder.async_add_executor_job(
                    history.state_changes_during_period, self.hass, start, now, ent, True, False, None, True)
                f = self._is_f(ent)
                series[ent] = [(st.last_changed.timestamp(), _temp(st, f)) for st in res.get(ent, [])]
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
                    local = dt_util.as_local(datetime.fromtimestamp(t, timezone.utc))
                    e = model.estimate(self.params, top, bot, volume=self.volume, shower_temp=self.shower_temp,
                                       month=local.month, head_flow=head, doy=local.timetuple().tm_yday,
                                       ema=self._ema_on(local.date().isoformat()))
                    v = round(e.minutes)
                    if v != last:
                        out.append([int(t * 1000), v, round(e.average, 2)])
                        last = v
                t += 300
            if out and out[-1][0] < int(now.timestamp() * 1000):
                out.append([int(now.timestamp() * 1000), last, out[-1][2]])
            return out

        result = await self.hass.async_add_executor_job(compute)
        self._hist_cache = (hours, now, result)
        return result if with_average else [[t, v] for t, v, _ in result]

    # ------------------------------------------------------------------ learning
    def _data(self) -> dict:
        return {
            "profile": self.profile, "params": self.params, "processed_until": self.processed_until,
            "last_cycle": asdict(self.last_cycle) if self.last_cycle else None, "last_learn": self.last_learn,
            "last_samples": self.last_samples, "full_ref": self.full_ref, "full_ref_avg": self.full_ref_avg,
            "full_ref_at": self.full_ref_at, "ledger": self.ledger.to_dict(),
            "habits": self.habits, "loss_samples": self.loss_samples, "drip_nights": self.drip_nights,
            "use_stats": self.use_stats, "loss_backfilled": self.loss_backfilled,
            "legionella_at": self.legionella_at,
            "learn_failures": self.learn_failures,
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
            self.learn_failures = 0
        except Exception:  # noqa: BLE001 - learning must never break the integration
            _LOGGER.exception("Learning failed; keeping the current model")
            self.learn_failures += 1
            self.request_save()
        finally:
            self.learning = False
            self._check_health()
            self.update()

    async def _learn(self) -> None:
        if self.outdoor:
            await self._refresh_ema()
        await self.ledger.async_refresh_cop(self)
        now = dt_util.utcnow()
        end = now - timedelta(minutes=30)
        start = now - timedelta(days=HISTORY_DAYS)
        if self.processed_until:
            start = max(start, datetime.fromtimestamp(self.processed_until * 60, timezone.utc) - timedelta(hours=6))
        recorder = get_instance(self.hass)
        series = await self._fetch_series(start, end)
        rows = await self.hass.async_add_executor_job(self._rows, series, start, end)
        if rows and not self.heating and not any(r.heating for r in rows):
            model.mark_heating_from_temperature(rows)
        await self._update_heat_loss(rows, end)
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

        def doy_of(minute: int) -> int:
            return dt_util.as_local(datetime.fromtimestamp(minute * 60, timezone.utc)).timetuple().tm_yday

        def ema_of(minute: int) -> float | None:
            return self._ema_on(dt_util.as_local(datetime.fromtimestamp(minute * 60, timezone.utc)).date().isoformat())

        result, last = await self.hass.async_add_executor_job(
            lambda: model.learn(self.params, rows, volume=self.volume, shower_temp=self.shower_temp,
                                since=self.processed_until, energy_by_hour=energy,
                                previous_cycle=self.last_cycle, month_of=month_of, water=water,
                                doy_of=doy_of, ema_of=ema_of if self.outdoor else None))
        self.params = result.params
        self.last_cycle = last

        self.processed_until = int(end.timestamp() // 60)
        self.last_learn = dt_util.now().isoformat(timespec="seconds")
        self.last_samples = result.samples
        await self._store.async_save(self._data())
        _LOGGER.debug("Learned from %d cycles / %d draws: %s", result.cycles, result.draws, result.samples)

    async def _update_heat_loss(self, rows: list[model.Row], end: datetime) -> None:
        """Heat-loss measurements and night drips from the rows just read. The first time (also after an
        update) from all the history there is, so the baseline is there right away."""
        if not self.loss_backfilled:
            rows = await self._fetch_rows(end - timedelta(days=HISTORY_DAYS), end)

        def local_of(minute: int) -> datetime:
            return dt_util.as_local(datetime.fromtimestamp(minute * 60, timezone.utc))
        samples, drips = await self.hass.async_add_executor_job(
            lambda: (insights.loss_samples(rows, volume=self.volume, local_of=local_of),
                     insights.night_drips(rows, local_of=local_of)))
        self.loss_samples = insights.merge_samples(self.loss_samples, samples)
        self.drip_nights = insights.merge_drips(self.drip_nights, drips)
        self.loss_backfilled = True
        self.loss = self._loss_status()
        self._sync_loss_issues()
        self.request_save()

    async def _fetch_series(self, start: datetime, end: datetime) -> dict[str, list]:
        recorder = get_instance(self.hass)
        series: dict[str, list] = {}
        for ent in (self.top, self.bottom, self.heating, self.water if self.flow_source == "water_meter" else None):
            if not ent:
                continue
            res = await recorder.async_add_executor_job(
                history.state_changes_during_period, self.hass, start, end, ent, True, False, None, True)
            series[ent] = res.get(ent, [])
        return series

    async def _fetch_rows(self, start: datetime, end: datetime) -> list[model.Row]:
        series = await self._fetch_series(start, end)
        rows = await self.hass.async_add_executor_job(self._rows, series, start, end)
        if rows and not any(r.heating for r in rows):
            model.mark_heating_from_temperature(rows)
        return rows

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
        ft, fb = self._is_f(self.top), self._is_f(self.bottom)
        top = [x for x in prep(self.top, lambda st: _temp(st, ft)) if x[1] is not None]   # skip invalid readings
        bottom = [x for x in prep(self.bottom, lambda st: _temp(st, fb)) if x[1] is not None] if self.bottom else []
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
