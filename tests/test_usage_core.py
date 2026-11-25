"""The hot water use tracker (usage.py) without Home Assistant: it imports nothing of it, and a shower played minute by
minute through it with a test clock and a stand-in host is booked as a shower. Runs in the plain test job (no
Home Assistant installed), so a tester's use can be replayed here in a fraction of a second."""
from __future__ import annotations

import ast
import importlib
import os
import sys
import types
from datetime import datetime, timedelta, timezone

HERE = os.path.dirname(__file__)
PKG_DIR = os.path.join(HERE, "..", "custom_components", "shower_time")
PURE = ("usage.py", "clock.py", "insights.py", "model.py", "const.py")


def _load(name: str):
    """A module of the integration as part of a stand-in package, so its __init__ (Home Assistant) is not run."""
    if "st_pure" not in sys.modules:
        pkg = types.ModuleType("st_pure")
        pkg.__path__ = [PKG_DIR]
        sys.modules["st_pure"] = pkg
    return importlib.import_module(f"st_pure.{name}")


def test_the_pure_parts_import_nothing_of_home_assistant() -> None:
    for f in PURE:
        tree = ast.parse(open(os.path.join(PKG_DIR, f)).read())
        for n in ast.walk(tree):
            mods = ([a.name for a in n.names] if isinstance(n, ast.Import)
                    else [n.module or ""] if isinstance(n, ast.ImportFrom) else [])
            assert not any(m.startswith("homeassistant") for m in mods), (f, mods)


class _Ledger:
    def __init__(self) -> None:
        self.session_start = None
        self.log: list[dict] = []
        self.other: list[dict] = []
        self.booked: list[tuple] = []

    def showers(self) -> list[dict]:
        return self.log

    def shower_ended(self, m, start, end, est, min_minutes=2.0, extra=None):
        entry = {"start": start.isoformat(), "minutes": round((end - start).total_seconds() / 60, 1),
                 "kind": "shower", **(extra or {})}
        self.log.append(entry)
        self.booked.append(("shower", entry))
        return entry

    def other_ended(self, m, kind, start, minutes, heat_kwh, **extra):
        entry = {"start": start.isoformat(), "kind": kind, "minutes": minutes, "heat_kwh": heat_kwh, **extra}
        self.other.append(entry)
        self.booked.append((kind, entry))
        return entry

    def bath_ended(self, m, start, duration, minutes, est, **extra):
        self.booked.append(("bath", {"minutes": minutes}))
        return {"kind": "bath"}

    def estimated_ended(self, m, start, minutes, est):
        self.booked.append(("estimated", {"minutes": minutes}))
        return {"kind": "shower"}


class _Host:
    """The manager as the tracker sees it (usage.UseHost), with the tank readings set by the test."""

    def __init__(self, model) -> None:
        self.model = model
        self.top, self.bottom = "sensor.top", "sensor.bottom"
        self.volume, self.bathtub = 200.0, True
        self.params = model.new_params("generic")
        self.ledger = _Ledger()
        self.estimate = None
        self.top_value = self.bottom_value = None
        self.reheating = False
        self.fired: list[str] = []

    def head_flow(self):
        return None, "unknown"

    def loss_k_h(self) -> float:
        return self.params["loss"]["v"]

    def tank_now(self):
        return self.top_value, self.bottom_value

    def notify(self) -> None: ...
    def save_later(self, delay: float) -> None: ...
    def request_save(self) -> None: ...

    def fire(self, kind: str, data: dict) -> None:
        self.fired.append(kind)

    def fire_entry(self, kind: str, entry: dict) -> None:
        self.fired.append(kind)


def test_a_shower_played_through_the_tracker_is_booked_as_a_shower() -> None:
    usage, clock, model = _load("usage"), _load("clock"), _load("model")
    now = [datetime(2026, 11, 20, 6, 30, tzinfo=timezone.utc)]
    clk = clock.FixedZoneClock(timezone(timedelta(hours=1)), lambda: now[0])   # CET, no tzdata needed
    h = _Host(model)
    t = usage.UseTracker(h, clk)

    def reading(top: float, bottom: float) -> None:      # what manager.update does with a new tank reading
        h.top_value, h.bottom_value = top, bottom
        t.detect()
        local = clk.now()
        h.estimate = model.estimate(h.params, top, bottom, volume=h.volume, shower_temp=38.0, month=local.month,
                                    doy=local.timetuple().tm_yday)
        t.reading()
        t.apply_resume()
        t.classify()
        t.track_run_out()

    reading(55.0, 50.0)
    bottom = 50.0
    for _ in range(8):                                    # 8 minutes: cold water comes in at the bottom
        now[0] += timedelta(minutes=1)
        bottom -= 2.0
        reading(55.0, bottom)
    assert t.showering and t.use is not None
    for _ in range(4 * 8):                                # 8 quiet minutes, the 15-second tick
        now[0] += timedelta(seconds=15)
        t.tick(now[0])
    assert not t.showering
    assert [k for k, _ in h.ledger.booked] == ["shower"]
    assert "shower_started" in h.fired and "shower_ended" in h.fired
