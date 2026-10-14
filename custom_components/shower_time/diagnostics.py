"""Diagnostics download: everything needed to reproduce a user's situation, nothing private.

Attach the downloaded file to a GitHub issue. It contains the settings (entity ids, no credentials), what
has been learned, the current estimate, the shower log and the tank readings of the last 24 hours at a
5-minute grid, so the situation can be replayed in the model's test simulator.
"""
from __future__ import annotations

from dataclasses import asdict
from datetime import timedelta
from typing import Any

from homeassistant.const import __version__ as HA_VERSION
from homeassistant.core import HomeAssistant
from homeassistant.loader import async_get_integration
from homeassistant.util import dt as dt_util

from . import ShowerTimeConfigEntry
from .const import DOMAIN


async def async_get_config_entry_diagnostics(hass: HomeAssistant, entry: ShowerTimeConfigEntry) -> dict[str, Any]:
    m = entry.runtime_data
    integration = await async_get_integration(hass, DOMAIN)
    e = m.estimate

    def unit(entity: str | None) -> str | None:
        st = hass.states.get(entity) if entity else None
        return st.attributes.get("unit_of_measurement") if st else None

    readings: list[list] = []
    try:
        end = dt_util.utcnow()
        start = end - timedelta(hours=24)
        series = await m._fetch_series(start, end)
        rows = await hass.async_add_executor_job(m._rows, series, start, end)
        for r in rows[::5]:
            readings.append([r.minute, round(r.top, 1), None if r.bottom is None else round(r.bottom, 1), int(r.heating)])
    except Exception as err:  # noqa: BLE001 - diagnostics must always download
        readings = [["error", str(err)]]

    return {
        "versions": {"integration": str(integration.version), "home_assistant": HA_VERSION},
        "config": {**entry.data, **entry.options},
        "units": {"top": unit(m.top), "bottom": unit(m.bottom), "outdoor": unit(m.outdoor), "price": unit(m.price),
                  "water": unit(m.water)},
        "live": {
            "top": m.top_value, "bottom": m.bottom_value, "reheating": m.reheating, "showering": m.showering,
            "live_offset_min": round(m.live_offset, 2), "estimate": asdict(e) if e else None,
            "fill_percent": m.fill_percent(), "full_reference_minutes": m.ref_minutes(),
            "full_reference_avg": m.full_ref_avg, "full_reference_at": m.full_ref_at,
            "since_reheat": m.since_reheat(), "standing_loss_per_day": m.standing_loss_per_day(),
            "outdoor_average_3w": m.ema_now, "cold_source": m.cold_source(), "head_flow": m.head_flow(),
        },
        "learned": {"params": m.params, "processed_until_minute": m.processed_until, "last_learn": m.last_learn,
                    "last_samples": m.last_samples, "last_cycle": asdict(m.last_cycle) if m.last_cycle else None},
        "ledger": {"cop": m.ledger.cop(m), "cop_learned": m.ledger.cop_learned, "tank_price": m.ledger.tank_price,
                   "currency": m.ledger.currency, "summary": m.ledger.summary(), "log": m.ledger.log[-30:],
                   "totals": m.ledger.totals, "covered_from": m.ledger.covered_from},
        "habits": m.habits,
        "reheats": {"log": m.reheat_log, "since": m.reheat_since},
        "forecast": f.as_dict() if (f := m.forecast()) else None,
        "heat_loss": {"status": m.loss, "samples": m.loss_samples[-30:], "drip_nights": m.drip_nights, "watts_now": m.heat_loss_now()},
        "legionella": {"last": m.legionella_at, "checked": m.legionella_checked, "overdue": m.legionella_overdue(),
                       "temperature": m.legionella_temp, "days": m.legionella_days},
        "health": {"learn_failures": m.learn_failures},
        "hot_water_use": {"bathroom_signal": m.bath, "signal_kind": m.bath_signal.kind if m.bath_signal else None,
                          "water_meter": m.water, "water_kind": m.water_signal.kind if m.water_signal else None,
                          "water_flow": round(m.water_signal.flow, 2) if m.water_signal else None,
                          "problems": m.problems(), "incomplete_days": m.habits.get("skip", []),
                          "confirm_k": m.confirm_k(), "use_stats": m.use_stats, "coil_events": m.coil_events,
                          "other": m.ledger.other[-30:]},
        "tank_readings_24h_5min": {"columns": ["minute_since_epoch_utc", "top_c", "bottom_c", "reheating"],
                                   "rows": readings},
    }
