"""Runtime glue: live estimate, nightly learning and persistence."""
from __future__ import annotations

import asyncio
from collections.abc import Callable
import copy
from dataclasses import asdict, replace
from datetime import datetime, timedelta, timezone
import logging
import math

from homeassistant.components.recorder import get_instance, history, statistics
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import STATE_UNAVAILABLE, STATE_UNKNOWN
from homeassistant.core import CALLBACK_TYPE, Event, HomeAssistant, callback
from homeassistant.helpers import entity_registry as er
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
from .fill import FillLevel
from .habits import Habits, Legionella
from .health import Health
from .learning import HeatLoss, Learner
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
    LEARN_HOUR,
    LEARN_MINUTE,
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
        self.fill = FillLevel(self)                 # 100 % and what went where since (fill.py)
        self.routine = Habits(self)                 # shower and reheat habits, today's forecast (habits.py)
        self.legionella = Legionella(self, float(cfg.get(CONF_LEGIONELLA_TEMP, DEFAULT_LEGIONELLA_TEMP)),
                                     float(cfg.get(CONF_LEGIONELLA_DAYS, DEFAULT_LEGIONELLA_DAYS)))
        self.health = Health(self)                  # repairs, problems for the card, downtime (health.py)
        self.heat = HeatLoss(self)                  # heat-loss measurements and night drips (learning.py)
        self.learner = Learner(self, _num, _temp, _local)   # the history and the nightly learning (learning.py)
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
        self._hist_lock = asyncio.Lock()
        self._loaded = False                        # the store was read (never save empty data over a good store)
        self._stopped = False                       # unloaded: nothing may save or change state any more

        self.estimate: model.Estimate | None = None
        self.top_value: float | None = None
        self.bottom_value: float | None = None
        self.reheating = False
        self._listeners: list[Callable[[], None]] = []
        self._event_listeners: list[Callable[[str, dict], None]] = []
        self._unsubs: list[CALLBACK_TYPE] = []
        # smart extras (insights.py): habits and today's forecast, heat loss and leaks, legionella, health
        # the signals that confirm a hot water use (a bathroom sensor, a water meter); followed in usage.UseTracker
        self.bath: str | None = cfg.get(CONF_BATH) or None
        self.bathtub = bool(cfg.get(CONF_BATHTUB, DEFAULT_BATHTUB))   # without a bath a fast fill is a bucket
        self._water_factor = 1.0

    # ------------------------------------------------------------------ lifecycle
    async def async_start(self) -> None:
        stored = await self._store.async_load()
        self._loaded = True
        if stored and stored.get("profile") == self.profile:
            self.params = model.ensure_params(stored["params"])
            self.learner.processed_until = stored.get("processed_until", 0)
            lc = stored.get("last_cycle")
            self.learner.last_cycle = model.Cycle(**lc) if lc else None
            self.learner.last_learn = stored.get("last_learn")
            self.learner.last_samples = stored.get("last_samples", {})
            self._apply_geometry()
            if stored.get("top_sensor") not in (None, self.top):
                # another top sensor (Reconfigure): where the old one sat says nothing about this one, so the
                # learner starts again from the preset; everything else learned stays
                self.params.pop("geo", None)
                self._apply_geometry()
        if stored:
            self.routine.habits = stored.get("habits") or self.routine.habits
            self.heat.load(stored)
            self.routine.reheat_log = stored.get("reheat_log") or {}
            self.routine.reheat_since = stored.get("reheat_since")
            self.heat.loss = self.heat.status()
            self.legionella.at = stored.get("legionella_at")
            self.legionella.since = stored.get("legionella_since")
            self.learner.learn_failures = stored.get("learn_failures", 0)
            self.health.alive_at = stored.get("alive_at")
            self.hot_top = stored.get("hot_top")
            self._hot_stored = self.hot_top is not None
            self._hot_at = dt_util.parse_datetime(stored["hot_at"]) if stored.get("hot_at") else None
            self._blur_stored = stored.get("blur") is not None
            self.blur = float(stored.get("blur") or 0.0)
            self._blur_share = stored.get("blur_share")
            self.usage.use_stats = {**self.usage.use_stats, **(stored.get("use_stats") or {})}
            self.ledger.load(stored.get("ledger"))
            self.fill.load(stored)
            self.usage.run_outs = stored.get("run_outs") or []
        if not stored and self.price and not self.ledger.currency:
            # a new installation: the unit of the monetary sensors is Home Assistant's own currency (ISO code) until
            # the price sensor says otherwise; existing ones keep what they had, whose statistics are in that unit
            self.ledger.currency = self.hass.config.currency or ""
        watch = [e for e in (self.top, self.bottom, self.heating, self.bath, self.water) if e]
        if stored:
            self.usage.restore(stored.get("resume"))
        self.health.note_downtime()
        self._resolve_signals()
        self._unsubs.append(async_track_state_change_event(self.hass, watch, self._state_changed))
        self._unsubs.append(async_track_state_change_event(self.hass, self._device_temperatures(), self._temp_changed))
        self._unsubs.append(async_track_time_change(self.hass, self._nightly, hour=LEARN_HOUR, minute=LEARN_MINUTE, second=0))
        self._unsubs.append(async_track_time_interval(self.hass, self.usage.tick, timedelta(seconds=15)))
        self._unsubs.append(async_track_time_change(self.hass, self._midnight, hour=0, minute=0, second=5))
        self._unsubs.append(async_track_time_interval(self.hass, self.health.check, timedelta(minutes=10)))
        self.entry.async_create_background_task(self.hass, self._start_ledger(), f"{DOMAIN}_ledger")
        if not self.learner.last_learn:                 # first install: learn from what the recorder already has
            self._unsubs.append(async_call_later(self.hass, 120, self._first_learn))
        if self.outdoor:
            self.entry.async_create_background_task(self.hass, self._refresh_ema(update=True), f"{DOMAIN}_ema")
        self.update()
        # find the last reheat in the history once; with the layer model also when its readings are not known yet
        # (updated from an older version), so the fill level and the hot layer are right straight away
        if self.full_ref is None or (self.params.get("geo") and (not self.full_ref_state or not self._hot_stored)):
            self.entry.async_create_background_task(self.hass, self._init_full_ref(), f"{DOMAIN}_full_ref")
        if self.legionella.since is None:
            self.legionella.since = dt_util.utcnow().isoformat()
        if self.legionella.at is None:          # the last legionella-proof heat-up, from the statistics
            self.entry.async_create_background_task(self.hass, self._init_legionella(), f"{DOMAIN}_legionella")
        else:
            self.legionella.checked = True
        self.health.sync_loss_issues()

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
        self.legionella.track()
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
            if self.full_ref_state and not self.reheating and not self.fill.was_heating:
                self.estimate = model.estimate(self.params, self.top_value, self.bottom_value, blur=self.blur,
                                               ref=self.full_ref_state, **kw)
            # minutes the blur took off: hot water turned lukewarm, its heat still in the tank (not lost to cooling)
            self.blurred_minutes = 0.0
            if self.blur > 0.0 and self.params.get("geo") and self.bottom_value is not None:
                sharp = model.estimate(self.params, self.top_value, self.bottom_value, **kw)
                self.blurred_minutes = max(0.0, sharp.minutes - free.minutes)
            # bounded outside a reheat (unbounded, a full tank cooling evenly creeps up and would set a new 100 %);
            # while heating and at its end unbounded, so a new 100 % is the layer model's own, as ref_minutes is
            self.fill.track(self.estimate.minutes)
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
        self.routine.update()
        self.notify()              # "today" counters start again

    # ------------------------------------------------------------------ shower habits and today's forecast


    # ------------------------------------------------------------------ habits, legionella, health (habits.py, health.py)
    # read by the entities and the diagnostics
    habits = property(lambda self: self.routine.habits)
    reheat_log = property(lambda self: self.routine.reheat_log)
    reheat_since = property(lambda self: self.routine.reheat_since)
    legionella_temp = property(lambda self: self.legionella.temp)
    legionella_days = property(lambda self: self.legionella.days)
    legionella_at = property(lambda self: self.legionella.at)
    legionella_checked = property(lambda self: self.legionella.checked)
    legionella_since = property(lambda self: self.legionella.since)

    def log_reheat(self, now: datetime) -> None:
        self.routine.log_reheat(now)

    def forecast(self) -> insights.Forecast | None:
        return self.routine.forecast()

    def forecast_attrs(self) -> dict | None:
        return self.routine.forecast_attrs()

    def legionella_overdue(self) -> bool | None:
        return self.legionella.overdue()

    def problems(self) -> list[dict]:
        return self.health.problems()

    async def _init_legionella(self) -> None:
        """The last hour in which the whole tank (top and bottom) reached the legionella temperature."""
        try:
            end = dt_util.utcnow()
            ids = {self.top} | ({self.bottom} if self.bottom else set())
            stats = await get_instance(self.hass).async_add_executor_job(
                statistics.statistics_during_period, self.hass, end - timedelta(days=45), end, ids, "hour",
                {"temperature": "°C"}, {"max"})

            self.legionella.from_statistics(stats)
        except Exception:  # noqa: BLE001
            _LOGGER.exception("Could not search the history for the last legionella heat-up")
        self.notify()

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
                rows = await self.learner.fetch_rows(end - timedelta(days=8), end)
                await self.ledger.async_backfill(self, rows)
            except Exception:  # noqa: BLE001
                _LOGGER.exception("Could not fill the shower log from the history")
        self.routine.update()
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

    async def _init_full_ref(self) -> None:
        """First run (and the first run of a version with the layer model): the minutes at the end of the last
        reheat in the past two days, the tank readings then, and the hot layer now, all from the history, so the
        fill level is right from the start."""
        try:
            points = await self.async_history(48, with_average=True)
        except Exception:  # noqa: BLE001
            points = []
        if not self._hot_stored and getattr(self, "_hist_hot", None) is not None:
            self.hot_top = max(self.hot_top or 0.0, self._hist_hot)
            self._hot_stored = True
        if not self._blur_stored and getattr(self, "_hist_blur", None) is not None:
            self.blur = self._hist_blur              # an update to this version: how long the tank stood, from history
            self._blur_stored = True
        if self.fill.from_history(points):
            self.save_later(5)
            self.update()

    # ------------------------------------------------------------------ the fill level (fill.FillLevel)
    # read by the entities, the ledger and the diagnostics
    full_ref = property(lambda self: self.fill.full_ref)
    full_ref_avg = property(lambda self: self.fill.full_ref_avg)
    full_ref_state = property(lambda self: self.fill.full_ref_state)
    full_ref_at = property(lambda self: self.fill.full_ref_at)
    top_ups = property(lambda self: self.fill.top_ups)

    def ref_minutes(self) -> float | None:
        return self.fill.ref_minutes()

    def since_reheat(self) -> dict | None:
        return self.fill.since_reheat()

    def standing_loss_per_day(self) -> dict:
        return self.fill.standing_loss_per_day()

    def fill_percent(self) -> int | None:
        return self.fill.fill_percent()

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
        self.health.alive_at = dt_util.utcnow().isoformat()
        avg = (model.tank_average(self.params, self.top_value, self.bottom_value)
               if self.top_value is not None and (not self.bottom or self.bottom_value is not None) else None)
        return {
            "top_sensor": self.top,
            "resume": {"at": self.health.alive_at, "top_sensor": self.top, "bottom_sensor": self.bottom, "top": self.top_value, "bottom": self.bottom_value, "avg": avg,
                       "use": self.usage.running_use()},
            "profile": self.profile, "params": self.params, "processed_until": self.learner.processed_until,
            "last_cycle": asdict(self.learner.last_cycle) if self.learner.last_cycle else None, "last_learn": self.learner.last_learn,
            "last_samples": self.learner.last_samples, "full_ref": self.full_ref, "full_ref_avg": self.full_ref_avg,
            "full_ref_state": self.full_ref_state,
            "full_ref_at": self.full_ref_at, "top_ups": self.top_ups, "ledger": self.ledger.to_dict(),
            "habits": self.routine.habits, "loss_samples": self.heat.loss_samples, "drip_nights": self.heat.drip_nights,
            "use_stats": self.use_stats, "loss_backfilled": self.heat.loss_backfilled,
            "reheat_log": self.routine.reheat_log, "reheat_since": self.routine.reheat_since,
            "legionella_at": self.legionella.at, "legionella_since": self.legionella.since,
            "learn_failures": self.learner.learn_failures, "alive_at": self.health.alive_at,
            "hot_top": self.hot_top, "hot_at": self._hot_at.isoformat() if self._hot_at else None,
            "blur": round(self.blur, 4), "blur_share": self._blur_share, "run_outs": self.run_outs,
        }

    async def _first_learn(self, _now) -> None:
        await self.learner.async_learn()

    async def _nightly(self, _now) -> None:
        await self.learner.async_learn()

    async def save_now(self) -> None:
        await self._store.async_save(self._data())

    @property
    def stopped(self) -> bool:
        return self._stopped

    def is_f(self, entity: str | None) -> bool:
        return self._is_f(entity)

    def ema_on(self, day: str) -> float | None:
        return self._ema_on(day)

    async def refresh_ema(self, update: bool = False) -> None:
        await self._refresh_ema(update)

    # ------------------------------------------------------------------ learning and heat loss (learning.py)
    # read by the entities, the ledger, the card and the diagnostics
    processed_until = property(lambda self: self.learner.processed_until)
    last_cycle = property(lambda self: self.learner.last_cycle)
    last_learn = property(lambda self: self.learner.last_learn)
    last_samples = property(lambda self: self.learner.last_samples)
    learning = property(lambda self: self.learner.learning)
    learn_failures = property(lambda self: self.learner.learn_failures)
    loss = property(lambda self: self.heat.loss)
    loss_samples = property(lambda self: self.heat.loss_samples)
    drip_nights = property(lambda self: self.heat.drip_nights)
    loss_backfilled = property(lambda self: self.heat.loss_backfilled)

    async def async_learn(self) -> None:
        await self.learner.async_learn()

    def loss_k_h(self) -> float:
        return self.heat.loss_k_h()

    def heat_loss_now(self) -> float | None:
        return self.heat.heat_loss_now()

