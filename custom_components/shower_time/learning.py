"""Learning from the history: the recorder read onto a one-minute grid, the nightly learning of the tank model
(``model.learn``, pure) with what the live part knows (uses followed live, the outdoor average, the energy meter), and
the heat-loss measurements and night drips (``HeatLoss``) that tell a leak or raised heat loss. ``Learner`` and
``HeatLoss`` own their state and reach the rest of the integration only through their host protocols."""
from __future__ import annotations

from collections.abc import Callable
from datetime import datetime, timedelta
import logging
from typing import Any, Protocol

from homeassistant.components.recorder import get_instance, history, statistics
from homeassistant.core import HomeAssistant
from homeassistant.util import dt as dt_util

from . import insights, model
from .clock import at as _at
from .const import HISTORY_DAYS

_LOGGER = logging.getLogger(__name__)


class HeatLossHost(Protocol):
    top_value: float | None
    bottom_value: float | None
    volume: float
    params: dict


class HeatLoss:
    """Heat-loss measurements (nights the tank only cooled) and night drips, and the status they give."""

    def __init__(self, host: HeatLossHost) -> None:
        self.h = host
        self.loss_samples: list[dict] = []
        self.drip_nights: list[dict] = []
        self.loss_backfilled = False                # the full history was searched once for heat-loss measurements
        self.loss: dict = self.status()

    def load(self, stored: dict) -> None:
        self.loss_samples = stored.get("loss_samples") or []
        self.drip_nights = stored.get("drip_nights") or []
        self.loss_backfilled = stored.get("loss_backfilled", False)

    def loss_k_h(self) -> float:
        """How fast the tank cools now: from its measured heat-loss coefficient (and how hot it is), else the
        standing loss the model learned."""
        t, b = self.h.top_value, self.h.bottom_value
        mean = (t + b) / 2 if t is not None and b is not None else t
        measured = insights.loss_k_per_h(self.loss.get("ua_recent"), mean, self.h.volume)
        return measured if measured is not None else self.h.params["loss"]["v"]

    def status(self) -> dict:
        st = insights.loss_status(self.loss_samples)
        st["dripping"] = insights.drip_status(self.drip_nights)
        st["leak_like"] = bool(st["leak_like"] or st["dripping"])
        return st

    def heat_loss_now(self) -> float | None:
        """Watts the tank loses right now, from its measured heat-loss coefficient."""
        if self.h.top_value is None:
            return None
        mean = (self.h.top_value + self.h.bottom_value) / 2 if self.h.bottom_value is not None else self.h.top_value
        return insights.heat_loss_watts(self.loss.get("ua_recent"), mean)


class LearnerHost(Protocol):
    hass: HomeAssistant
    top: str
    bottom: str | None
    heating: str | None
    heating_states: set[str]
    water: str | None
    flow_source: str
    energy: str | None
    outdoor: str | None
    volume: float
    shower_temp: float
    params: dict                                # replaced by what was learned
    ledger: Any                                 # ledger.ShowerLedger
    routine: Any                                # habits.Habits: the reheat log the history fills
    health: Any                                 # health.Health: the heat-loss repairs
    heat: HeatLoss
    stopped: bool                               # unloaded: nothing may change any more

    def is_f(self, entity: str | None) -> bool: ...
    async def refresh_ema(self, update: bool = False) -> None: ...
    def ema_on(self, day: str) -> float | None: ...
    def confirm_k(self) -> float: ...
    def fill_rate(self) -> float: ...
    def draw_flow(self) -> float | None: ...
    def request_save(self) -> None: ...
    async def save_now(self) -> None: ...
    def update(self) -> None: ...


class Learner:
    """The nightly learning and the history reads it (and the card's history, the ledger) are built on."""

    def __init__(self, host: LearnerHost, num: Callable, temp: Callable, local: Callable) -> None:
        self.h = host
        self._num, self._temp, self._local = num, temp, local     # the manager's state readers (°F, invalid values)
        self.processed_until: int = 0
        self.last_cycle: model.Cycle | None = None
        self.last_learn: str | None = None
        self.last_samples: dict = {}
        self.learning = False
        self.learn_failures = 0

    async def async_learn(self) -> None:
        if self.learning or self.h.stopped:
            return
        self.learning = True
        try:
            await self._learn()
            if not self.h.stopped:
                self.learn_failures = 0
        except Exception:  # noqa: BLE001 - learning must never break the integration
            if self.h.stopped:
                return
            _LOGGER.exception("Learning failed; keeping the current model")
            self.learn_failures += 1
            self.h.request_save()
        finally:
            self.learning = False
            if not self.h.stopped:
                self.h.health.check()
                self.h.update()

    async def _learn(self) -> None:
        if self.h.outdoor:
            await self.h.refresh_ema()
        await self.h.ledger.async_refresh_cop(self.h)
        if self.h.stopped:
            return
        now = dt_util.utcnow()
        end = now - timedelta(minutes=30)
        start = now - timedelta(days=HISTORY_DAYS)
        if self.processed_until:
            start = max(start, _at(self.processed_until) - timedelta(hours=6))
        recorder = get_instance(self.h.hass)
        series = await self.fetch_series(start, end)
        rows = await self.h.hass.async_add_executor_job(self.rows, series, start, end)
        if self.h.stopped:
            return
        if rows and not self.h.heating and not any(r.heating for r in rows):
            model.mark_heating_from_temperature(rows)
        await self._update_heat_loss(rows, end)
        if self.h.stopped or len(rows) < 600:
            _LOGGER.info("Not enough history to learn yet (%d minutes)", len(rows))
            return
        energy = None
        if self.h.energy:
            stats = await recorder.async_add_executor_job(
                statistics.statistics_during_period, self.h.hass, start, end, {self.h.energy}, "hour",
                {"energy": "kWh"}, {"change"})
            energy = {}
            for row in stats.get(self.h.energy, []):
                ts = row["start"]
                ts = ts.timestamp() if isinstance(ts, datetime) else float(ts)
                energy[int(ts // 60)] = float(row.get("change") or 0.0)
            if self.h.stopped:
                return

        water = self._water_series(series.get(self.h.water, [])) if self.h.water and self.h.flow_source == "water_meter" else None

        def ema_of(minute: int) -> float | None:
            return self.h.ema_on(self._local(minute).date().isoformat())

        confirm_k = self.h.confirm_k()
        fill_rate = self.h.fill_rate()
        # what a use was followed live as (with the bathroom and water signals): a tap, or a bath or bucket being
        # filled. A bath the water meter told apart can fall slower than the fill rate the history judges by (#10)
        live = [(datetime.fromisoformat(e["start"]).timestamp() / 60, "tap" if e["kind"] == "tap" else "fill")
                for e in self.h.ledger.other + self.h.ledger.baths() if e.get("kind") in ("tap", "fill", "bath")]

        def draw_kind(d: model.Draw) -> str:            # only real showers teach shower length and flow
            if kind := next((k for t, k in live if abs(d.start - t) <= 5), None):
                return kind
            local = self._local(d.start)
            cold = model.cold_for(self.h.params, local.month, local.timetuple().tm_yday,
                                  ema_of(d.start) if self.h.outdoor else None)
            return insights.draw_kind(self.h.params, rows, d.start, d.minutes, cold, confirm_k, fill_rate,
                                      model.shower_minute(self.h.params, cold, self.h.shower_temp, self.h.volume))

        result, last = await self.h.hass.async_add_executor_job(
            lambda: model.learn(self.h.params, rows, volume=self.h.volume, shower_temp=self.h.shower_temp,
                                since=self.processed_until, energy_by_hour=energy, previous_cycle=self.last_cycle,
                                month_of=lambda t: self._local(t).month, doy_of=lambda t: self._local(t).timetuple().tm_yday,
                                water=water, ema_of=ema_of if self.h.outdoor else None, draw_kind=draw_kind,
                                draw_flow=self.h.draw_flow()))
        if self.h.stopped:                               # unloaded while learning: the final save is already written
            return
        self.h.params = result.params
        self.last_cycle = last

        self.processed_until = int(end.timestamp() // 60)
        self.last_learn = dt_util.now().isoformat(timespec="seconds")
        self.last_samples = result.samples
        await self.h.save_now()
        _LOGGER.debug("Learned from %d cycles / %d draws: %s", result.cycles, result.draws, result.samples)

    async def _update_heat_loss(self, rows: list[model.Row], end: datetime) -> None:
        """Heat-loss measurements and night drips from the rows just read. The first time (also after an
        update) from all the history there is, so the baseline is there right away."""
        first = not self.h.heat.loss_backfilled or self.h.routine.reheat_since is None
        if first:
            rows = await self.fetch_rows(end - timedelta(days=HISTORY_DAYS), end)

        samples, drips, reheats = await self.h.hass.async_add_executor_job(
            lambda: (insights.loss_samples(rows, volume=self.h.volume, local_of=self._local),
                     insights.night_drips(rows, local_of=self._local),
                     insights.reheat_starts(rows, local_of=self._local)))
        for day, hours in reheats.items():                  # the reheats the history shows, with the live ones
            self.h.routine.reheat_log[day] = sorted(set(self.h.routine.reheat_log.get(day, [])) | set(hours))
        if first and rows:
            self.h.routine.reheat_since = (self._local(rows[0].minute).date() + timedelta(days=1)).isoformat()
        self.h.routine.prune_reheat_log()
        self.h.routine.update_reheat_habits()
        self.h.heat.loss_samples = insights.merge_samples(self.h.heat.loss_samples, samples)
        self.h.heat.drip_nights = insights.merge_drips(self.h.heat.drip_nights, drips)
        self.h.heat.loss_backfilled = True
        self.h.heat.loss = self.h.heat.status()
        self.h.health.sync_loss_issues()
        self.h.request_save()

    async def fetch_series(self, start: datetime, end: datetime) -> dict[str, list]:
        recorder = get_instance(self.h.hass)
        series: dict[str, list] = {}
        for ent in (self.h.top, self.h.bottom, self.h.heating, self.h.water if self.h.flow_source == "water_meter" else None):
            if not ent:
                continue
            res = await recorder.async_add_executor_job(
                history.state_changes_during_period, self.h.hass, start, end, ent, True, False, None, True)
            series[ent] = res.get(ent, [])
        return series

    async def fetch_rows(self, start: datetime, end: datetime) -> list[model.Row]:
        series = await self.fetch_series(start, end)
        rows = await self.h.hass.async_add_executor_job(self.rows, series, start, end)
        if rows and not any(r.heating for r in rows):
            model.mark_heating_from_temperature(rows)
        return rows

    def _water_series(self, states: list) -> tuple[str, list[tuple[int, float]]] | None:
        """Water meter history as ("rate", L/min) or ("total", litres), whatever the meter reports."""
        st = self.h.hass.states.get(self.h.water)
        unit = str((st.attributes.get("unit_of_measurement") if st else "") or "").replace(" ", "").lower()
        rates = {"l/min": 1.0, "l/h": 1 / 60, "m³/h": 1000 / 60, "m3/h": 1000 / 60, "gal/min": 3.785}
        totals = {"l": 1.0, "m³": 1000.0, "m3": 1000.0, "gal": 3.785}
        if unit in rates:
            kind, factor = "rate", rates[unit]
        elif unit in totals:
            kind, factor = "total", totals[unit]
        else:
            _LOGGER.warning("Water meter %s has unit %r; expected a flow (L/min) or a volume (L, m³)", self.h.water, unit)
            return None
        out = []
        for s in states:
            v = self._num(s)
            if v is not None:
                out.append((int(s.last_changed.timestamp() // 60), v * factor))
        return kind, out

    def rows(self, series: dict[str, list], start: datetime, end: datetime) -> list[model.Row]:
        """Forward-fill all series onto a one-minute grid."""
        def prep(ent, conv):
            out = []
            for st in series.get(ent, []):
                out.append((int(st.last_changed.timestamp() // 60), conv(st)))
            return out
        ft, fb = self.h.is_f(self.h.top), self.h.is_f(self.h.bottom)
        top = [x for x in prep(self.h.top, lambda st: self._temp(st, ft)) if x[1] is not None]   # skip invalid readings
        bottom = [x for x in prep(self.h.bottom, lambda st: self._temp(st, fb)) if x[1] is not None] if self.h.bottom else []
        heat = prep(self.h.heating, lambda s: str(s.state).lower() in self.h.heating_states) if self.h.heating else []
        idx = {"t": 0, "b": 0, "h": 0}
        cur = {"t": None, "b": None, "h": False}
        rows: list[model.Row] = []
        for m in range(int(start.timestamp() // 60), int(end.timestamp() // 60)):
            for key, seq in (("t", top), ("b", bottom), ("h", heat)):
                while idx[key] < len(seq) and seq[idx[key]][0] <= m:
                    cur[key] = seq[idx[key]][1]
                    idx[key] += 1
            if cur["t"] is None or (self.h.bottom and cur["b"] is None):
                continue
            rows.append(model.Row(minute=m, top=cur["t"], bottom=cur["b"] if self.h.bottom else None, heating=bool(cur["h"])))
        return rows
