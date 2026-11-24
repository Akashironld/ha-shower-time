"""Runtime glue: live estimate, nightly learning and persistence."""
from __future__ import annotations

import asyncio
from collections.abc import Callable
import copy
from dataclasses import asdict, replace
from datetime import date, datetime, timedelta, timezone
import logging
import math

from homeassistant.components.recorder import get_instance, history, statistics
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import STATE_UNAVAILABLE, STATE_UNKNOWN
from homeassistant.core import CALLBACK_TYPE, Event, HomeAssistant, callback
from homeassistant.helpers import entity_registry as er, issue_registry as ir
from homeassistant.helpers.event import (
    async_call_later,
    async_track_state_change_event,
    async_track_time_change,
    async_track_time_interval,
)
from homeassistant.helpers.storage import Store
from homeassistant.util import dt as dt_util

from . import insights, model
from .clock import at as _at, now_min as _now_min
from .usage import UseTracker
from .ledger import ShowerLedger
from .const import (
    CONF_BATH,
    CONF_BATHTUB,
    CONF_BOTTOM,
    CONF_COP,
    CONF_ELEC,
    CONF_ENERGY,
    CONF_PRICE,
    DEFAULT_BATHTUB,
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
    CONF_H_BOTTOM,
    CONF_H_INLET,
    CONF_H_OUTLET,
    CONF_H_TOP,
    CONF_LEARN_HEIGHTS,
    CONF_TANK_TYPE,
    DEFAULT_LEARN_HEIGHTS,
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


def _local(minute: float) -> datetime:
    return dt_util.as_local(_at(minute))


def _ema_lookup(table: dict[str, float], day: str) -> float | None:
    """The outdoor average on ``day``, or on the last day before it that is known."""
    if not table:
        return None
    if day in table:
        return table[day]
    earlier = [d for d in table if d <= day]
    return table[max(earlier)] if earlier else None


REHEAT_MIN_RISE = 2.0           # K the tank average must gain to count as a reheat
REHEAT_MAX_DIP = 0.4            # K it may sag inside one reheat (a shower during it) without ending the band
REHEAT_MAX_GAP_MS = 30 * 60_000


def reheat_events(points: list[list[float]]) -> list[dict]:
    """Reheats as bands from the history series ([ms, minutes, tank average, ...] where the minutes change):
    stretches where the average climbs clearly, joined when they are close, one band each."""
    out: list[dict] = []
    run: list | None = None                       # [start ms, end ms, start avg, peak avg]
    prev = None
    for p in points:
        if len(p) < 3 or p[2] is None:
            continue
        t, avg = int(p[0]), float(p[2])
        if prev is not None:
            rising = avg - prev[1] > 0.05
            if run is not None and (t - run[1] > REHEAT_MAX_GAP_MS or avg < run[3] - REHEAT_MAX_DIP):
                if run[3] - run[2] >= REHEAT_MIN_RISE:
                    out.append({"start": run[0], "end": run[1], "kind": "reheat"})
                run = None
            if rising:
                if run is None:
                    run = [prev[0], t, prev[1], avg]
                elif avg > run[3]:
                    run[1], run[3] = t, avg
        prev = (t, avg)
    if run is not None and run[3] - run[2] >= REHEAT_MIN_RISE:
        out.append({"start": run[0], "end": run[1], "kind": "reheat"})
    return out




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
        # a COP filled in is always used; empty (or the old default 2.5 that the form used to fill in) = learn it
        cop = float(cfg.get(CONF_COP) or 0)
        self.cop_manual: float | None = cop if cop and cop != DEFAULT_COP else None
        self.ledger = ShowerLedger()
        self.usage = UseTracker(self)              # the hot water use going on (usage.py)
        self._ema_by_day: dict[str, float] = {}       # local date -> outdoor running average
        self.ema_now: float | None = None
        self.heating: str | None = cfg.get(CONF_HEATING) or None
        self.heating_states = {s.strip().lower() for s in str(cfg.get(CONF_HEATING_STATES, DEFAULT_HEATING_STATES)).split(",") if s.strip()}
        self.energy: str | None = cfg.get(CONF_ENERGY) or None
        self.low_minutes: float = float(cfg.get(CONF_LOW, DEFAULT_LOW))
        self.profile: str = cfg.get(CONF_PROFILE, "generic")
        # where the tank sensors sit (layer model); entries from before 0.14 get the Weheat tank they have
        self.tank_type: str = cfg.get(CONF_TANK_TYPE) or self._guess_tank_type()
        heights = {"outlet": cfg.get(CONF_H_OUTLET), "top": cfg.get(CONF_H_TOP), "bottom": cfg.get(CONF_H_BOTTOM),
                   "inlet": cfg.get(CONF_H_INLET)}
        self.geo_setup: dict | None = (model.new_geometry(self.tank_type, heights,
                                                          bool(cfg.get(CONF_LEARN_HEIGHTS, DEFAULT_LEARN_HEIGHTS)))
                                       if self.bottom else None)
        self.hot_top: float | None = None           # the hot layer: hottest top since the last reheat
        self.blur: float = 0.0                      # how far a part-used tank's hot/cold boundary has blurred
        self._blur_stored = False
        self.blurred_minutes = 0.0                  # minutes that blur takes off the layer model now
        self._blur_share: float | None = None       # how part-used the tank was at the last reading
        self._hot_at: datetime | None = None
        self._hot_stored = False                    # the hot layer came from the store (else: from the history)

        self._store: Store = Store(hass, STORAGE_VERSION, f"{DOMAIN}.{entry.entry_id}")
        self.params: dict = model.new_params(self.profile)
        self._apply_geometry()
        self.processed_until: int = 0
        self.last_cycle: model.Cycle | None = None
        self.last_learn: str | None = None
        self.last_samples: dict = {}
        self.learning = False
        self._hist_lock = asyncio.Lock()
        self._loaded = False                        # the store was read (never save empty data over a good store)
        self._stopped = False                       # unloaded: nothing may save or change state any more

        self.estimate: model.Estimate | None = None
        self.top_value: float | None = None
        self.bottom_value: float | None = None
        self.reheating = False
        # 100 % = the shower minutes right after the last reheat; the fill level drops with use and standing loss
        self.full_ref: float | None = None          # minutes at the last reheat (kept for display/compat)
        self.full_ref_avg: float | None = None      # tank average temperature right after the last reheat
        self.full_ref_state: list[float] | None = None   # top and bottom right after the last reheat
        self.full_ref_at: str | None = None
        self.top_ups: list[dict] = []           # partial reheats since the last complete one
        self._heat_from: tuple[datetime, float] | None = None
        self._ref_low: float | None = None
        self._last_minutes: float | None = None
        self._listeners: list[Callable[[], None]] = []
        self._event_listeners: list[Callable[[str, dict], None]] = []
        self._unsubs: list[CALLBACK_TYPE] = []
        # smart extras (insights.py): habits and today's forecast, heat loss and leaks, legionella, health
        self.habits: dict = insights.new_habits()
        self.loss_samples: list[dict] = []
        self.drip_nights: list[dict] = []
        self.loss_backfilled = False                # the full history was searched once for heat-loss measurements
        self.reheat_log: dict[str, list[int]] = {}  # local day -> hours in which a reheat started
        self.reheat_since: str | None = None        # first day the reheat log is complete from
        self._last_reheat_start: datetime | None = None
        self.loss: dict = self._loss_status()
        self.legionella_temp = float(cfg.get(CONF_LEGIONELLA_TEMP, DEFAULT_LEGIONELLA_TEMP))
        self.legionella_days = float(cfg.get(CONF_LEGIONELLA_DAYS, DEFAULT_LEGIONELLA_DAYS))
        self.legionella_at: str | None = None
        self.legionella_checked = False             # the history was searched (so "never" means never)
        self.legionella_since: str | None = None    # how far back we can tell: the oldest statistic, or when we began
        self.learn_failures = 0
        self._missing_since: dict[str, datetime] = {}
        self._swap_since: datetime | None = None
        # the signals that confirm a hot water use (a bathroom sensor, a water meter); followed in usage.UseTracker
        self.bath: str | None = cfg.get(CONF_BATH) or None
        self.bathtub = bool(cfg.get(CONF_BATHTUB, DEFAULT_BATHTUB))   # without a bath a fast fill is a bucket
        self._water_factor = 1.0
        self.alive_at: str | None = None            # saved every 10 minutes: shows when Home Assistant was off

    # ------------------------------------------------------------------ lifecycle
    async def async_start(self) -> None:
        stored = await self._store.async_load()
        self._loaded = True
        if stored and stored.get("profile") == self.profile:
            self.params = model.ensure_params(stored["params"])
            self.processed_until = stored.get("processed_until", 0)
            lc = stored.get("last_cycle")
            self.last_cycle = model.Cycle(**lc) if lc else None
            self.last_learn = stored.get("last_learn")
            self.last_samples = stored.get("last_samples", {})
            self._apply_geometry()
            if stored.get("top_sensor") not in (None, self.top):
                # another top sensor (Reconfigure): where the old one sat says nothing about this one, so the
                # learner starts again from the preset; everything else learned stays
                self.params.pop("geo", None)
                self._apply_geometry()
        if stored:
            self.habits = stored.get("habits") or self.habits
            self.loss_samples = stored.get("loss_samples") or []
            self.drip_nights = stored.get("drip_nights") or []
            self.loss_backfilled = stored.get("loss_backfilled", False)
            self.reheat_log = stored.get("reheat_log") or {}
            self.reheat_since = stored.get("reheat_since")
            self.loss = self._loss_status()
            self.legionella_at = stored.get("legionella_at")
            self.legionella_since = stored.get("legionella_since")
            self.learn_failures = stored.get("learn_failures", 0)
            self.alive_at = stored.get("alive_at")
            self.hot_top = stored.get("hot_top")
            self._hot_stored = self.hot_top is not None
            self._hot_at = dt_util.parse_datetime(stored["hot_at"]) if stored.get("hot_at") else None
            self._blur_stored = stored.get("blur") is not None
            self.blur = float(stored.get("blur") or 0.0)
            self._blur_share = stored.get("blur_share")
            self.usage.use_stats = {**self.usage.use_stats, **(stored.get("use_stats") or {})}
            self.ledger.load(stored.get("ledger"))
            self.full_ref_avg = stored.get("full_ref_avg")
            self.full_ref_state = stored.get("full_ref_state")
            self.full_ref_at = stored.get("full_ref_at")
            self.top_ups = stored.get("top_ups") or []
            self.usage.run_outs = stored.get("run_outs") or []
            # a reference in minutes only (before 0.5.1) depends on the model it was computed with: rebuild it
            self.full_ref = stored.get("full_ref") if self.full_ref_avg is not None else None
        if not stored and self.price and not self.ledger.currency:
            # a new installation: the unit of the monetary sensors is Home Assistant's own currency (ISO code) until
            # the price sensor says otherwise; existing ones keep what they had, whose statistics are in that unit
            self.ledger.currency = self.hass.config.currency or ""
        watch = [e for e in (self.top, self.bottom, self.heating, self.bath, self.water) if e]
        if stored:
            self.usage.restore(stored.get("resume"))
        self._note_downtime()
        self._resolve_signals()
        self._unsubs.append(async_track_state_change_event(self.hass, watch, self._state_changed))
        self._unsubs.append(async_track_state_change_event(self.hass, self._device_temperatures(), self._temp_changed))
        self._unsubs.append(async_track_time_change(self.hass, self._nightly, hour=LEARN_HOUR, minute=LEARN_MINUTE, second=0))
        self._unsubs.append(async_track_time_interval(self.hass, self.usage.tick, timedelta(seconds=15)))
        self._unsubs.append(async_track_time_change(self.hass, self._midnight, hour=0, minute=0, second=5))
        self._unsubs.append(async_track_time_interval(self.hass, self._check_health, timedelta(minutes=10)))
        self.entry.async_create_background_task(self.hass, self._start_ledger(), f"{DOMAIN}_ledger")
        if not self.last_learn:                 # first install: learn from what the recorder already has
            self._unsubs.append(async_call_later(self.hass, 120, self._first_learn))
        if self.outdoor:
            self.entry.async_create_background_task(self.hass, self._refresh_ema(update=True), f"{DOMAIN}_ema")
        self.update()
        # find the last reheat in the history once; with the layer model also when its readings are not known yet
        # (updated from an older version), so the fill level and the hot layer are right straight away
        if self.full_ref is None or (self.params.get("geo") and (not self.full_ref_state or not self._hot_stored)):
            self.entry.async_create_background_task(self.hass, self._init_full_ref(), f"{DOMAIN}_full_ref")
        if self.legionella_since is None:
            self.legionella_since = dt_util.utcnow().isoformat()
        if self.legionella_at is None:          # the last legionella-proof heat-up, from the statistics
            self.entry.async_create_background_task(self.hass, self._init_legionella(), f"{DOMAIN}_legionella")
        else:
            self.legionella_checked = True
        self._sync_loss_issues()






    async def async_stop(self) -> None:
        self._stopped = True
        for unsub in self._unsubs:
            unsub()
        self._unsubs.clear()
        # write now: a reload after an options change must find the latest state, and a delayed save of this
        # manager must not land over the new one (async_save cancels the pending delayed write)
        if self._loaded:
            await self._store.async_save(self._data())

    def save_later(self, delay: float) -> None:
        if not self._stopped:
            self._store.async_delay_save(self._data, delay)

    @callback
    def notify(self) -> None:
        for cb in list(self._listeners):
            cb()

    def add_listener(self, cb: Callable[[], None]) -> Callable[[], None]:
        self._listeners.append(cb)
        return lambda: self._listeners.remove(cb)

    @callback
    def add_event_listener(self, cb: Callable[[str, dict], None]) -> Callable[[], None]:
        """Shower started / ended, for the event entity."""
        self._event_listeners.append(cb)
        return lambda: self._event_listeners.remove(cb)

    @callback
    def fire(self, kind: str, data: dict) -> None:
        for cb in list(self._event_listeners):
            cb(kind, data)

    def fire_entry(self, kind: str, entry: dict) -> None:
        self.fire(kind, {**entry, "currency": self.ledger.currency or None})

    def _is_f(self, entity: str | None) -> bool:
        st = self.hass.states.get(entity) if entity else None
        unit = str((st.attributes.get("unit_of_measurement") if st else "") or "").replace(" ", "")
        return unit in ("°F", "F", "℉")

    def tank_now(self) -> tuple[float | None, float | None]:
        """The tank sensors as Home Assistant has them now (°C), before an update reads them."""
        top = _temp(self.hass.states.get(self.top), self._is_f(self.top))
        bottom = _temp(self.hass.states.get(self.bottom), self._is_f(self.bottom)) if self.bottom else None
        return top, bottom

    # ------------------------------------------------------------------ the hot water use (usage.UseTracker)
    # read by the entities, the ledger and the diagnostics
    showering = property(lambda self: self.usage.showering)
    use = property(lambda self: self.usage.use)
    live_offset = property(lambda self: self.usage.live_offset)
    run_outs = property(lambda self: self.usage.run_outs)
    use_stats = property(lambda self: self.usage.use_stats)
    coil_events = property(lambda self: self.usage.coil_events)
    bath_signal = property(lambda self: self.usage.bath_signal)
    water_signal = property(lambda self: self.usage.water_signal)

    def use_kind(self) -> str | None:
        return self.usage.use_kind()

    def shower_stopped_at(self) -> datetime | None:
        return self.usage.shower_stopped_at()

    def confirm_k(self) -> float:
        return self.usage.confirm_k()

    def fill_rate(self) -> float:
        return self.usage.fill_rate()

    def bath_active(self) -> bool:
        return self.usage.bath_active()

    def water_active(self) -> bool:
        return self.usage.water_active()

    def fast_flow(self) -> bool:
        return self.usage.fast_flow()

    def signal_active(self) -> bool:
        return self.usage.signal_active()

    # ------------------------------------------------------------------ sensor heights
    def _guess_tank_type(self) -> str:
        ent = er.async_get(self.hass).async_get(self.top)
        if ent is not None and ent.platform == "weheat":
            return {200: "weheat_wbl200", 300: "weheat_wbl300"}.get(round(self.volume), "other")
        return "other"

    def _apply_geometry(self) -> None:
        """Use the sensor heights from the settings; keep what was learned while the settings stay the same."""
        if not model.same_geometry_setup(self.params.get("geo"), self.geo_setup):
            self.params["geo"] = copy.deepcopy(self.geo_setup)
        elif self.geo_setup is None:
            self.params.pop("geo", None)

    def _follow_hot_layer(self) -> None:
        now = dt_util.utcnow()
        minutes = (now - self._hot_at).total_seconds() / 60 if self._hot_at else 0.0
        self.hot_top = model.hot_layer(self.params, self.top_value, self.hot_top, minutes, self.reheating)
        self.blur = model.spread(self.top_value, self.bottom_value, self.blur, minutes, self._blur_share)
        self._blur_share = model.part_used(self.top_value, self.bottom_value)
        self._hot_at = now

    def draw_flow(self) -> float | None:
        """L/min a shower draws at the shower temperature, when known (a fixed temperature and a known head)."""
        return self.head_flow()[0] if self.temp_mode == "fixed" else None

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

    def _device_temperatures(self) -> list[str]:
        """The temperature sensors on the device(s) of the tank sensors: they show when the device last sent data
        (a tank sensor that does not change says nothing about that). Just the tank sensors without a device."""
        reg = er.async_get(self.hass)
        devices = {e.device_id for e in (reg.async_get(x) for x in (self.top, self.bottom) if x) if e and e.device_id}
        found = [e.entity_id for e in reg.entities.values() if e.device_id in devices and
                 (e.device_class or e.original_device_class) == "temperature"]
        return sorted(set(found) | {x for x in (self.top, self.bottom) if x})

    @callback
    def _temp_changed(self, _event: Event) -> None:
        self.usage.note_temp()

    @callback
    def update(self) -> None:
        self.top_value = _temp(self.hass.states.get(self.top), self._is_f(self.top))
        self.bottom_value = _temp(self.hass.states.get(self.bottom), self._is_f(self.bottom)) if self.bottom else None
        if self.top_value is None or (self.bottom and self.bottom_value is None):
            self.usage.note_unavailable()
        if self.heating:
            st = self.hass.states.get(self.heating)
            self.reheating = bool(st and str(st.state).lower() in self.heating_states)
        self._resolve_signals()
        self._feed_bath()
        self._feed_water()
        self.usage.detect()
        self._track_legionella()
        if self.top_value is None:
            self.estimate = None
        else:
            head = self.head_flow()[0] if self.temp_mode == "auto" else None
            now = dt_util.now()
            self._follow_hot_layer()
            kw = dict(volume=self.volume, shower_temp=self.shower_temp, month=now.month, head_flow=head,
                      doy=now.timetuple().tm_yday, ema=self.ema_now, hot=self.hot_top, draw_flow=self.draw_flow())
            free = model.estimate(self.params, self.top_value, self.bottom_value, blur=self.blur, **kw)
            self.estimate = free
            # a full tank loses at least the minutes its heat loss since the last reheat is worth; not while it heats
            # (the state it is bounded by is the previous reheat's until this one ends)
            if self.full_ref_state and not self.reheating and not getattr(self, "_was_heating", False):
                self.estimate = model.estimate(self.params, self.top_value, self.bottom_value, blur=self.blur,
                                               ref=self.full_ref_state, **kw)
            # minutes the blur took off: hot water turned lukewarm, its heat still in the tank (not lost to cooling)
            self.blurred_minutes = 0.0
            if self.blur > 0.0 and self.params.get("geo") and self.bottom_value is not None:
                sharp = model.estimate(self.params, self.top_value, self.bottom_value, **kw)
                self.blurred_minutes = max(0.0, sharp.minutes - free.minutes)
            # bounded outside a reheat (unbounded, a full tank cooling evenly creeps up and would set a new 100 %);
            # while heating and at its end unbounded, so a new 100 % is the layer model's own, as ref_minutes is
            self._track_full(self.estimate.minutes)
            self.ledger.on_estimate(self, self.estimate)
            self.usage.reading()
            self.usage.apply_resume()
            self.usage.classify()
            self.usage.track_run_out()
        self.notify()

    # ------------------------------------------------------------------ what kind of hot water use
    @callback
    def _resolve_signals(self) -> None:
        """Follow the water meter and the bathroom signal once their kind is known. It follows from the unit of a
        real state: at startup a sensor may not be loaded yet, or unavailable without its unit (#10)."""
        if self.water and self.water_signal is None:
            kind = self._water_kind()
            if kind is not None:
                self.usage.water_signal = insights.WaterSignal(kind[0])
                self._water_factor = kind[1]
        if self.bath and self.bath_signal is None:
            kind = self._bath_kind()
            if kind is not None:
                self.usage.bath_signal = insights.BathSignal(kind)
                self.entry.async_create_background_task(self.hass, self._seed_bath(), f"{DOMAIN}_bath")

    def _bath_kind(self) -> str | None:
        domain = self.bath.split(".")[0]
        if domain != "sensor":
            return "on_off"
        st = self.hass.states.get(self.bath)
        if st is None or st.state in (STATE_UNAVAILABLE, STATE_UNKNOWN):
            return None
        dc = st.attributes.get("device_class")
        unit = str(st.attributes.get("unit_of_measurement") or "")
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
            self.bath_signal.feed(_now_min(), self._bath_value(self.hass.states.get(self.bath)))

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

    def _water_kind(self) -> tuple[str, float] | None:
        st = self.hass.states.get(self.water)
        if st is None or st.state in (STATE_UNAVAILABLE, STATE_UNKNOWN):
            return None
        unit = str(st.attributes.get("unit_of_measurement") or "").replace(" ", "").lower()
        rates = {"l/min": 1.0, "l/h": 1 / 60, "m³/h": 1000 / 60, "m3/h": 1000 / 60, "gal/min": 3.785}
        totals = {"l": 1.0, "m³": 1000.0, "m3": 1000.0, "gal": 3.785}
        if unit in rates:
            return "rate", rates[unit]
        if unit in totals:
            return "total", totals[unit]
        return None

    @callback
    def _feed_water(self) -> None:
        if self.water_signal is not None:
            v = _num(self.hass.states.get(self.water))
            self.water_signal.feed(_now_min(), None if v is None else v * self._water_factor)

    @callback
    def _midnight(self, _now) -> None:
        self._update_habits()
        self.notify()              # "today" counters start again

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
            if insights.is_incomplete(self.habits, day.isoformat()):
                self.habits["last_day"] = day.isoformat()          # showers may have been missed: skip the day
            else:
                insights.add_day(self.habits, day.weekday(), day.isoformat(), insights.minutes_by_hour(by_day.get(day, [])))
            day += timedelta(days=1)
            changed = True
        if changed:
            self.request_save()
        self._update_reheat_habits()

    # ------------------------------------------------------------------ reheat habits
    @callback
    def _log_reheat(self, now: datetime) -> None:
        if self._last_reheat_start is not None and now - self._last_reheat_start < timedelta(hours=1):
            return                                          # one reheat in pieces counts once
        self._last_reheat_start = now
        local = dt_util.as_local(now)
        day = local.date().isoformat()
        self.reheat_log[day] = sorted(set(self.reheat_log.get(day, [])) | {local.hour})
        if self.reheat_since is None:
            self.reheat_since = (local.date() + timedelta(days=1)).isoformat()
        self._prune_reheat_log()
        self.request_save()

    def _prune_reheat_log(self) -> None:
        cutoff = (dt_util.now().date() - timedelta(days=LOG_DAYS + 6)).isoformat()
        self.reheat_log = {d: h for d, h in self.reheat_log.items() if d >= cutoff}

    @callback
    def _update_reheat_habits(self) -> None:
        """Add every completed day since the reheat log is complete to the reheat profile."""
        if not self.reheat_since:
            return
        today = dt_util.now().date()
        r = self.habits.get("reheat") or {}
        day = date.fromisoformat(self.reheat_since)
        if r.get("last_day"):
            day = max(day, date.fromisoformat(r["last_day"]) + timedelta(days=1))
        day = max(day, today - timedelta(days=LOG_DAYS + 5))
        changed = False
        while day < today:
            iso = day.isoformat()
            if not insights.is_incomplete(self.habits, iso):
                insights.add_reheat_day(self.habits, self.reheat_log.get(iso, []))
            self.habits.setdefault("reheat", {"days": 0, "hours": [0.0] * 24})["last_day"] = iso
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
                                 minutes_now=minutes, per_minute=e.per_minute, loss_k_h=self.loss_k_h(),
                                 typical=self.params["duration"]["v"])

    def loss_k_h(self) -> float:
        """How fast the tank cools now: from its measured heat-loss coefficient (and how hot it is), else the
        standing loss the model learned."""
        t, b = self.top_value, self.bottom_value
        mean = (t + b) / 2 if t is not None and b is not None else t
        measured = insights.loss_k_per_h(self.loss.get("ua_recent"), mean, self.volume)
        return measured if measured is not None else self.params["loss"]["v"]

    def forecast_attrs(self) -> dict | None:
        """Today's forecast with clock times instead of hours (for attributes and notifications)."""
        f = self.forecast()
        if f is None:
            return None
        now = dt_util.now()
        day = now.replace(hour=0, minute=0, second=0, microsecond=0)
        return {"enough": f.enough, "expected_minutes": f.expected_minutes, "available_minutes": f.available_minutes,
                "reheat_chance": f.reheat_chance,
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
            # "no hit" only means something for as long as the statistics reach back (of both sensors)
            first = max(min(top), min(bot)) if bot else min(top)
            old = dt_util.parse_datetime(self.legionella_since) if self.legionella_since else None
            if old is None or first < old.timestamp():
                self.legionella_since = datetime.fromtimestamp(first, timezone.utc).isoformat()
                self.request_save()
            hits = [t for t, v in top.items() if v >= self.legionella_temp
                    and (bot is None or bot.get(t, -99.0) >= self.legionella_temp)]
            if hits and self.legionella_at is None:
                self.legionella_at = dt_util.as_local(datetime.fromtimestamp(max(hits) + 3599, timezone.utc)).isoformat(timespec="minutes")
                self.request_save()
            self.legionella_checked = True
        except Exception:  # noqa: BLE001
            _LOGGER.exception("Could not search the history for the last legionella heat-up")
        self.notify()

    def legionella_overdue(self) -> bool | None:
        if not self.legionella_checked:
            return None
        if self.legionella_at is None:
            # no heat-up found: overdue only once we could have seen one for the whole period (a tank with a few
            # days of statistics, or just installed, has not been proven overdue)
            since = dt_util.parse_datetime(self.legionella_since) if self.legionella_since else None
            if since is None or dt_util.utcnow() - since < timedelta(days=self.legionella_days):
                return None
            return True
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
    def _note_downtime(self) -> None:
        """Home Assistant was off for a while: the days in between may miss showers, so the habits skip them."""
        if self.alive_at:
            last = dt_util.parse_datetime(self.alive_at)
            if last is not None and dt_util.utcnow() - last > timedelta(hours=1):
                day, today = dt_util.as_local(last).date(), dt_util.now().date()
                days = []
                while day <= today:
                    days.append(day.isoformat())
                    day += timedelta(days=1)
                insights.mark_incomplete(self.habits, days)
        self.alive_at = dt_util.utcnow().isoformat()

    def problems(self) -> list[dict]:
        """What needs attention, for the card: a possible leak, raised heat loss, legionella, a silent sensor."""
        out = []
        if self.loss.get("leak_like"):
            out.append({"key": "leak"})
        elif self.loss.get("raised"):
            out.append({"key": "heat_loss", "ratio": self.loss.get("ratio")})
        if self.legionella_overdue():
            days = None
            if self.legionella_at:
                days = (dt_util.now() - datetime.fromisoformat(self.legionella_at)).days
            out.append({"key": "legionella", "days": days})
        now = dt_util.utcnow()
        if any(now - t >= timedelta(hours=2) for t in self._missing_since.values()):
            out.append({"key": "sensor"})
        return out

    @callback
    def _check_health(self, _now=None) -> None:
        now = dt_util.utcnow()
        self.alive_at = now.isoformat()
        self.request_save()
        for key, ent, val in (("top", self.top, self.top_value), ("bottom", self.bottom, self.bottom_value)):
            if not ent:
                self._issue(f"sensor_missing:{key}", False)     # a sensor emptied under Configure
                continue
            if val is None:
                since = self._missing_since.setdefault(key, now)
                self._issue(f"sensor_missing:{key}", now - since >= timedelta(hours=2), {"entity": ent})
                if now - since >= timedelta(hours=2):        # showers may be missed today
                    insights.mark_incomplete(self.habits, [dt_util.now().date().isoformat()])
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
        self.save_later(30)

    async def _start_ledger(self) -> None:
        await self.ledger.async_refresh_cop(self)
        if self.ledger.needs_reprice(self) or any(
                not MIN_SHOWER_MINUTES <= e["minutes"] <= MAX_SHOWER_MINUTES for e in self.ledger.log
                if e.get("source") == "history"):
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
        return _ema_lookup(self._ema_by_day, day)

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
        # the readings themselves: with the layer model the tank average alone does not give back the minutes
        self.full_ref_state = ([self.top_value, self.bottom_value]
                               if self.top_value is not None and self.bottom_value is not None else None)
        self.full_ref_at = dt_util.now().isoformat(timespec="seconds")
        self.top_ups = []
        self._ref_low = minutes
        self.save_later(60)

    @callback
    def _track_full(self, m: float) -> None:
        """Keep 100 % at the end of the latest *complete* reheat.

        A reheat that leaves the tank evenly hot (top and bottom within 2.5 °C) and about as full as last time
        (90 %) sets a new 100 %; so does one the day after, also when it heats to a lower temperature (smart
        controllers vary the target). Anything less is a top-up: it is kept in ``top_ups`` (when, how much) and
        "since reheat" goes on from the last complete reheat. A top-up never becomes the new 100 % unless it
        beats the old one.
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
        if heating and not was:
            self._log_reheat(now)
            self._heat_from = (now, prev if prev is not None else m)
        if was and not heating:                 # a reheat just finished
            even = self.bottom_value is None or (self.top_value is not None and self.top_value - self.bottom_value <= 2.5)
            since = dt_util.parse_datetime(self.full_ref_at) if self.full_ref_at else None
            old = since is None or now - since > timedelta(hours=20)
            if m > ref_now or (even and (m >= 0.9 * ref_now or old)):
                self._set_ref(m)
            elif self._heat_from is not None:
                start, before = self._heat_from
                self.top_ups = (self.top_ups + [{
                    "at": dt_util.as_local(start).isoformat(timespec="minutes"),
                    "until": dt_util.as_local(now).isoformat(timespec="minutes"),
                    "added_minutes": round(max(0.0, m - before), 1),
                    "top": round(self.top_value, 1) if self.top_value is not None else None}])[-10:]
                self.request_save()
            self._heat_from = None
            self._rise_at = None
            return
        # sensors still settling after the reheat state switched off. Not for less than a minute: with the layer
        # model a full tank's minutes rise a little when the top reads 0.1 K lower before the bottom updates
        if not heating and m > ref_now + 1.0:
            self._set_ref(m)

    def ref_minutes(self) -> float | None:
        """Minutes the tank held right after the last reheat, with the *current* model (so the fill level does
        not jump when the model learns or the cold water estimate changes)."""
        e = self.estimate
        if e is None:
            return self.full_ref
        if self.full_ref_state and self.params.get("geo"):
            # layer model: the minutes in the tank as it was then (its empty point moves with the tank's state)
            top, bottom = self.full_ref_state
            now = dt_util.now()
            return model.estimate(self.params, top, bottom, volume=self.volume, shower_temp=self.shower_temp,
                                  month=now.month, doy=now.timetuple().tm_yday, ema=self.ema_now, hot=top,
                                  draw_flow=self.draw_flow()).minutes
        if self.full_ref_avg is not None:
            return max(0.0, (self.full_ref_avg - e.empty_at) / e.per_minute)
        return self.full_ref

    def since_reheat(self) -> dict | None:
        """Where the hot water went since the last complete reheat: left, showered, other hot water (a bath, the
        sink) and lost to cooling, plus what a reheat in between added.

        Showers count in minutes (one shower minute takes about one minute off the estimate), other hot water by
        the heat it took. Cooling is the tank's standing loss over the time since the reheat, or the drop that is
        left unexplained when that is more. When the tank holds more than that adds up to and a reheat was seen in
        between, it put heat back in: that is ``reheated_minutes``. Without a reheat the uses are cut back to what
        the tank lost (``overcounted_minutes``). Left + showered + other + cooling = reference + reheated."""
        e, ref = self.current, self.ref_minutes()
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
        showered = sum(x["minutes"] for x in self.ledger.showers() if x["start"] >= key)
        per_min_kwh = e.per_minute * self.volume * model.CP
        # a bath is a session of its own, but not a shower: it stays with the tap and bucket it was while it ran (#10)
        bath = sum(x["heat_kwh"] for x in self.ledger.baths() if x["start"] >= key) / per_min_kwh
        other = bath + sum(x["heat_kwh"] for x in self.ledger.other if x["start"] >= key) / per_min_kwh
        kind = self.use_kind()
        if kind == "shower" and self.ledger.session_start:
            until = self.shower_stopped_at() or dt_util.utcnow()      # the meter saw the shower stop: no further
            showered += max(0.0, (until - self.ledger.session_start).total_seconds() / 60)
        elif kind not in (None, "coil") and self.use is not None:
            other += self.use.drawn / e.per_minute       # a bath or tap running now: the heat it took so far
        standing = self.loss_k_h() * hours / e.per_minute
        heating_now = getattr(self, "_was_heating", False) and self._heat_from is not None   # a top-up going on now
        s = insights.split_since_reheat(ref, e.minutes, showered, other, standing, bool(self.top_ups) or heating_now)
        cooling, reheated = s["cooling"], s["reheated"]
        # hot water that turned lukewarm while the tank stood counts as cooled minutes, but its heat is still there
        heat = max(min(cooling, standing), cooling - self.blurred_minutes) * per_min_kwh
        price = self.ledger.tank_price
        if price is None:
            p, _ = self.ledger.price_now(self)
            price = p / self.ledger.cop(self) if p is not None else None
        last_top_up = self.top_ups[-1]["at"] if self.top_ups else None
        if heating_now:
            last_top_up = dt_util.as_local(self._heat_from[0]).isoformat(timespec="minutes")
        return {"since": self.full_ref_at, "hours": round(hours, 1), "reference_minutes": round(ref, 1),
                "top_ups": self.top_ups, "last_top_up_at": last_top_up,
                "remaining_minutes": round(e.minutes, 1), "showered_minutes": round(s["showered"], 1),
                "other_minutes": round(s["other"], 1),
                "bath_minutes": round(bath * s["other"] / other, 1) if other else 0.0,   # its share of other
                "cooling_minutes": round(cooling, 1), "lukewarm_minutes": round(min(cooling, self.blurred_minutes), 1),
                "reheated_minutes": round(reheated, 1), "total_minutes": round(ref + reheated, 1),
                "overcounted_minutes": round(s["overcounted"], 1),
                "cooling_kwh_heat": round(heat, 2),
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
        """First run (and the first run of a version with the layer model): the minutes at the end of the last
        reheat in the past two days, the tank readings then, and the hot layer now, all from the history, so the
        fill level is right from the start."""
        try:
            points = await self.async_history(48, with_average=True)
        except Exception:  # noqa: BLE001
            points = []
        ref = low = prev = ts = avg = state = None
        for t, v, a, top, bot in points:        # same rules as _track_full, replayed over the history
            if ref is None:
                ref = low = prev = v
                ts, avg, state = t, a, [top, bot]
                continue
            low = min(low, v)
            if (v > prev and v >= low + 3) or v > ref:
                ref, ts, low, avg, state = v, t, v, a, [top, bot]
            prev = v
        if not self._hot_stored and getattr(self, "_hist_hot", None) is not None:
            self.hot_top = max(self.hot_top or 0.0, self._hist_hot)
            self._hot_stored = True
        if not self._blur_stored and getattr(self, "_hist_blur", None) is not None:
            self.blur = self._hist_blur              # an update to this version: how long the tank stood, from history
            self._blur_stored = True
        self.full_ref_state = state if state and None not in state else None
        if ref is None and self.estimate:
            ref = self.estimate.minutes
        if ref is not None:
            self.full_ref = float(ref)
            self.full_ref_avg = avg if avg is not None else (
                self.estimate.empty_at + float(ref) * self.estimate.per_minute if self.estimate else None)
            self.full_ref_at = (dt_util.as_local(datetime.fromtimestamp(ts / 1000, timezone.utc)).isoformat(timespec="seconds")
                                if ts else dt_util.now().isoformat(timespec="seconds"))
            self._ref_low = self.estimate.minutes if self.estimate else ref
            self.save_later(5)
            self.update()

    # ------------------------------------------------------------------ history for the card
    async def async_history(self, hours: int = 24, with_average: bool = False) -> list[list[float]]:
        """Shower minutes over the last ``hours``, recomputed with the current model from the tank sensors.

        Works from day one: the tank sensors usually have far more history than this integration.
        Returns [[timestamp_ms, minutes], ...] every 5 minutes (only where the value changes); ``with_average``
        adds the tank average, the top and the bottom reading to each point.
        """
        async with self._hist_lock:                 # concurrent requests wait for one computation, then use its cache
            now = dt_util.utcnow()
            cached = getattr(self, "_hist_cache", None)
            if not (cached and cached[0] == hours and (now - cached[1]).total_seconds() < 300):
                await self._compute_history(hours, now)
            result = self._hist_cache[2]
        return result if with_average else [p[:2] for p in result]

    async def _compute_history(self, hours: int, now: datetime) -> None:
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

        rate = self.draw_flow()
        # the executor works on copies: learning changes the model on the event loop meanwhile
        params, ema_table = copy.deepcopy(self.params), dict(self._ema_by_day)
        volume, shower_temp, top_ent, bottom_ent = self.volume, self.shower_temp, self.top, self.bottom
        hot_state: list = []

        avgs: list[list[float]] = []                # tank average every 5 minutes: reheats are read off this

        def compute() -> list[list[float]]:
            out, i, j, top, bot, last, hot, blur, ref = [], 0, 0, None, None, None, None, 0.0, None
            avgs.clear()
            tops, bots = series.get(top_ent, []), series.get(bottom_ent, []) if bottom_ent else []
            t = start.timestamp()
            while t <= now.timestamp():
                while i < len(tops) and tops[i][0] <= t:
                    top = tops[i][1] if tops[i][1] is not None else top
                    i += 1
                while j < len(bots) and bots[j][0] <= t:
                    bot = bots[j][1] if bots[j][1] is not None else bot
                    j += 1
                if top is not None and (not bottom_ent or bot is not None):
                    local = dt_util.as_local(datetime.fromtimestamp(t, timezone.utc))
                    if bot is not None and (hot is None or top >= hot + 0.3) and model.part_used(top, bot) <= 0.0:
                        ref = (top, bot)            # rising past the hot layer on a full tank: a reheat (not 0.1 K noise)
                    hot = model.hot_layer(params, top, hot, 5.0, False)
                    blur = model.spread(top, bot, blur, 5.0)
                    e = model.estimate(params, top, bot, volume=volume, shower_temp=shower_temp,
                                       month=local.month, head_flow=head, doy=local.timetuple().tm_yday,
                                       ema=_ema_lookup(ema_table, local.date().isoformat()), hot=hot, draw_flow=rate,
                                       blur=blur, ref=ref)
                    v = round(e.minutes)
                    avgs.append([int(t * 1000), v, round(e.average, 2)])
                    if v != last:
                        out.append([int(t * 1000), v, round(e.average, 2), top, bot])
                        last = v
                t += 300
            if out and out[-1][0] < int(now.timestamp() * 1000):
                out.append([int(now.timestamp() * 1000), last, *out[-1][2:]])
            hot_state.append(hot)
            hot_state.append(blur)
            return out

        result = await self.hass.async_add_executor_job(compute)
        # the hot layer and the blur now, as the history shows them
        self._hist_hot, self._hist_blur = (hot_state[-2], hot_state[-1]) if hot_state else (None, None)
        self._hist_cache = (hours, now, result, avgs)


    async def async_events(self, hours: int = 24) -> dict:
        """The card's history: ``points`` (as async_history) and ``events``, the bands drawn over it: showers and
        baths from the log, other hot water, and reheats read off the tank average in the same series."""
        full = await self.async_history(hours, with_average=True)
        end = dt_util.utcnow().timestamp() * 1000
        begin = end - hours * 3600_000
        cache = self._hist_cache                     # no await since the history was returned: still its cache
        events = reheat_events((cache[3] if cache[0] == hours else None) or full)
        for kind, entries in (("log", self.ledger.log), ("other", self.ledger.other)):
            for e in entries:
                try:
                    start = datetime.fromisoformat(e["start"]).timestamp() * 1000
                    length = float(e.get("duration") or e.get("minutes") or 0)
                except (KeyError, TypeError, ValueError):
                    continue
                if length <= 0:
                    continue
                k = e.get("kind") or "shower"
                events.append({"start": int(start), "end": int(start + length * 60_000),
                               "kind": k if kind == "log" and k in ("shower", "bath") else "other"})
        events = sorted((x for x in events if x["end"] >= begin and x["start"] <= end), key=lambda x: x["start"])
        return {"points": [p[:2] for p in full], "events": events}

    # ------------------------------------------------------------------ learning
    def _data(self) -> dict:
        # stamped at every write: after a restart this is when Home Assistant went away
        self.alive_at = dt_util.utcnow().isoformat()
        avg = (model.tank_average(self.params, self.top_value, self.bottom_value)
               if self.top_value is not None and (not self.bottom or self.bottom_value is not None) else None)
        return {
            "top_sensor": self.top,
            "resume": {"at": self.alive_at, "top_sensor": self.top, "bottom_sensor": self.bottom, "top": self.top_value, "bottom": self.bottom_value, "avg": avg,
                       "use": self.usage.running_use()},
            "profile": self.profile, "params": self.params, "processed_until": self.processed_until,
            "last_cycle": asdict(self.last_cycle) if self.last_cycle else None, "last_learn": self.last_learn,
            "last_samples": self.last_samples, "full_ref": self.full_ref, "full_ref_avg": self.full_ref_avg,
            "full_ref_state": self.full_ref_state,
            "full_ref_at": self.full_ref_at, "top_ups": self.top_ups, "ledger": self.ledger.to_dict(),
            "habits": self.habits, "loss_samples": self.loss_samples, "drip_nights": self.drip_nights,
            "use_stats": self.use_stats, "loss_backfilled": self.loss_backfilled,
            "reheat_log": self.reheat_log, "reheat_since": self.reheat_since,
            "legionella_at": self.legionella_at, "legionella_since": self.legionella_since,
            "learn_failures": self.learn_failures, "alive_at": self.alive_at,
            "hot_top": self.hot_top, "hot_at": self._hot_at.isoformat() if self._hot_at else None,
            "blur": round(self.blur, 4), "blur_share": self._blur_share, "run_outs": self.run_outs,
        }

    async def _first_learn(self, _now) -> None:
        await self.async_learn()

    async def _nightly(self, _now) -> None:
        await self.async_learn()

    async def async_learn(self) -> None:
        if self.learning or self._stopped:
            return
        self.learning = True
        try:
            await self._learn()
            if not self._stopped:
                self.learn_failures = 0
        except Exception:  # noqa: BLE001 - learning must never break the integration
            if self._stopped:
                return
            _LOGGER.exception("Learning failed; keeping the current model")
            self.learn_failures += 1
            self.request_save()
        finally:
            self.learning = False
            if not self._stopped:
                self._check_health()
                self.update()

    async def _learn(self) -> None:
        if self.outdoor:
            await self._refresh_ema()
        await self.ledger.async_refresh_cop(self)
        if self._stopped:
            return
        now = dt_util.utcnow()
        end = now - timedelta(minutes=30)
        start = now - timedelta(days=HISTORY_DAYS)
        if self.processed_until:
            start = max(start, _at(self.processed_until) - timedelta(hours=6))
        recorder = get_instance(self.hass)
        series = await self._fetch_series(start, end)
        rows = await self.hass.async_add_executor_job(self._rows, series, start, end)
        if self._stopped:
            return
        if rows and not self.heating and not any(r.heating for r in rows):
            model.mark_heating_from_temperature(rows)
        await self._update_heat_loss(rows, end)
        if self._stopped or len(rows) < 600:
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
            if self._stopped:
                return

        water = self._water_series(series.get(self.water, [])) if self.water and self.flow_source == "water_meter" else None

        def ema_of(minute: int) -> float | None:
            return self._ema_on(_local(minute).date().isoformat())

        confirm_k = self.confirm_k()
        fill_rate = self.fill_rate()
        # what a use was followed live as (with the bathroom and water signals): a tap, or a bath or bucket being
        # filled. A bath the water meter told apart can fall slower than the fill rate the history judges by (#10)
        live = [(datetime.fromisoformat(e["start"]).timestamp() / 60, "tap" if e["kind"] == "tap" else "fill")
                for e in self.ledger.other + self.ledger.baths() if e.get("kind") in ("tap", "fill", "bath")]

        def draw_kind(d: model.Draw) -> str:            # only real showers teach shower length and flow
            if kind := next((k for t, k in live if abs(d.start - t) <= 5), None):
                return kind
            local = _local(d.start)
            cold = model.cold_for(self.params, local.month, local.timetuple().tm_yday,
                                  ema_of(d.start) if self.outdoor else None)
            return insights.draw_kind(self.params, rows, d.start, d.minutes, cold, confirm_k, fill_rate,
                                      model.shower_minute(self.params, cold, self.shower_temp, self.volume))

        result, last = await self.hass.async_add_executor_job(
            lambda: model.learn(self.params, rows, volume=self.volume, shower_temp=self.shower_temp,
                                since=self.processed_until, energy_by_hour=energy, previous_cycle=self.last_cycle,
                                month_of=lambda t: _local(t).month, doy_of=lambda t: _local(t).timetuple().tm_yday,
                                water=water, ema_of=ema_of if self.outdoor else None, draw_kind=draw_kind,
                                draw_flow=self.draw_flow()))
        if self._stopped:                               # unloaded while learning: the final save is already written
            return
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
        first = not self.loss_backfilled or self.reheat_since is None
        if first:
            rows = await self._fetch_rows(end - timedelta(days=HISTORY_DAYS), end)

        samples, drips, reheats = await self.hass.async_add_executor_job(
            lambda: (insights.loss_samples(rows, volume=self.volume, local_of=_local),
                     insights.night_drips(rows, local_of=_local),
                     insights.reheat_starts(rows, local_of=_local)))
        for day, hours in reheats.items():                  # the reheats the history shows, with the live ones
            self.reheat_log[day] = sorted(set(self.reheat_log.get(day, [])) | set(hours))
        if first and rows:
            self.reheat_since = (_local(rows[0].minute).date() + timedelta(days=1)).isoformat()
        self._prune_reheat_log()
        self._update_reheat_habits()
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
