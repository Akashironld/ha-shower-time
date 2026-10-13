"""Tests for the hot water model against a simulated stratified tank.

Run with:  python3 -m pytest tests   (or: python3 tests/test_model.py)
"""
import os
import random
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "custom_components", "shower_time"))

import model  # noqa: E402

CP = model.CP


class Tank:
    """Plug-flow tank of N layers; cold water enters at the bottom, hot leaves at the top."""

    def __init__(self, volume=200.0, layers=40, temp=55.0, cold=15.0, bottom_at=0.12, top_at=0.80):
        self.v, self.n, self.cold = volume, layers, cold
        self.t = [temp] * layers                       # index 0 = bottom
        self.ib, self.it = int(bottom_at * layers), int(top_at * layers)

    def sensors(self):
        return self.t[self.it], self.t[self.ib]

    def average(self):
        return sum(self.t) / self.n

    def draw(self, litres, mix_share=0.25):
        """Hot water leaves at the top; the cold inlet jet mixes into the lower ``mix_share`` of the tank."""
        layer = self.v / self.n
        mix = max(1, int(mix_share * self.n))
        while litres > 1e-9:
            take = min(litres, layer)
            frac = take / layer
            for i in range(self.n - 1, 0, -1):          # shift up by a fraction of a layer
                self.t[i] = self.t[i] * (1 - frac) + self.t[i - 1] * frac
            self.t[0] = self.t[0] * (1 - frac) + self.cold * frac
            zone = sum(self.t[:mix]) / mix              # inlet turbulence
            for i in range(mix):
                self.t[i] = self.t[i] * 0.7 + zone * 0.3
            litres -= take

    def shower(self, flow, shower_temp=38.0):
        """One minute of showering at ``flow`` L/min mixed water. Returns False when it runs cold."""
        out = self.t[-1]
        if out < shower_temp:
            return False
        self.draw(flow * (shower_temp - self.cold) / (out - self.cold))
        return True

    def heat(self, target, kwh_per_min=0.1):
        """Coil near the bottom: mix and heat the whole tank towards ``target``. Returns kWh used."""
        avg = self.average()
        if avg >= target - 0.05:
            self.t = [max(x, target) for x in self.t]
            return 0.0
        dk = min(kwh_per_min / (CP * self.v), target - avg)
        self.t = [avg + dk] * self.n
        return dk * CP * self.v

    def lose(self, k_per_min):
        self.t = [x - k_per_min for x in self.t]


def simulate(days=21, flow=6.0, seed=1, cold=15.0):
    rnd = random.Random(seed)
    tank = Tank(cold=cold)
    rows, energy = [], {}
    heating_until = None
    start = 29_000_000                                  # arbitrary minute since epoch
    plan = {}
    for d in range(days):
        for _ in range(rnd.choice([1, 2, 2, 3])):       # evening showers
            m = d * 1440 + 20 * 60 + rnd.randint(0, 150)
            plan[m] = rnd.randint(7, 13)
        if rnd.random() < 0.5:                          # sometimes a morning shower
            plan[d * 1440 + 7 * 60 + rnd.randint(0, 40)] = rnd.randint(6, 10)
    busy_until = -1
    for m in range(days * 1440):
        minute = start + m
        heating = False
        if m % 1440 == 13 * 60:
            heating_until = minute + 120
        if heating_until and minute <= heating_until:
            e = tank.heat(55.0)
            if e > 0:
                heating = True
                energy[minute // 60 * 60] = energy.get(minute // 60 * 60, 0.0) + e
            else:
                heating_until = None
        if m in plan:
            busy_until = m + plan[m]
        if m < busy_until and not heating:
            tank.shower(flow)
        tank.lose(0.25 / 60)
        top, bottom = tank.sensors()
        rows.append(model.Row(minute=minute, top=round(top, 1), bottom=round(bottom, 1), heating=heating))
    return rows, energy, tank


def month_of(_minute):
    return 10


def test_learns_flow_towards_truth():
    rows, energy, _ = simulate(flow=6.0)
    p = model.new_params("generic")
    for k in p["cold"]:
        p["cold"][k] = 15.0
    res, _last = model.learn(p, rows, volume=200, shower_temp=38, since=0, energy_by_hour=energy,
                             previous_cycle=None, month_of=month_of)
    learned = res.params["flow"]["v"]
    assert res.samples["flow"] >= 8, res.samples
    # the prior (7.5) must clearly move towards the true 6.0
    assert learned < 7.2, learned
    print("flow learned", learned, "samples", res.samples)


def test_learns_without_energy_sensor():
    rows, _energy, _ = simulate(flow=6.0, seed=3)
    for r in rows:
        r.heating = False                               # no heat-source entity either
    p = model.new_params("generic")
    for k in p["cold"]:
        p["cold"][k] = 15.0
    res, _ = model.learn(p, rows, volume=200, shower_temp=38, since=0, energy_by_hour=None,
                         previous_cycle=None, month_of=month_of)
    assert res.cycles >= 15, res.cycles                 # reheats found from temperature alone
    assert res.params["flow"]["v"] < 7.4, res.params["flow"]


def test_countdown_tracks_real_shower():
    """During a shower the estimate should fall roughly one minute per minute."""
    tank = Tank(cold=15.0)
    p = model.new_params("generic")
    for k in p["cold"]:
        p["cold"][k] = 15.0
    p["flow"]["v"] = 6.0
    est = lambda: model.estimate(p, *tank.sensors(), volume=200, shower_temp=38, month=10).minutes
    before = est()
    for _ in range(10):
        tank.shower(6.0)
    after = est()
    assert 5 <= before - after <= 16, (before, after)


def test_runs_cold_when_estimate_says_empty():
    """Shower until the water runs cold; the estimate at the start should be in the right range."""
    tank = Tank(cold=15.0)
    p = model.new_params("generic")
    for k in p["cold"]:
        p["cold"][k] = 15.0
    p["flow"]["v"] = 6.0
    predicted = model.estimate(p, *tank.sensors(), volume=200, shower_temp=38, month=10).minutes
    real = 0
    while tank.shower(6.0) and real < 300:
        real += 1
    assert 0.6 * real <= predicted <= 1.4 * real, (predicted, real)


def test_single_sensor_mode():
    p = model.new_params("generic")
    e = model.estimate(p, 55.0, None, volume=200, shower_temp=38, month=1)
    assert e.minutes > 10 and e.hot_fraction == 1.0
    e = model.estimate(p, 39.0, None, volume=200, shower_temp=38, month=1)
    assert e.minutes == 0


if __name__ == "__main__":
    for name, fn in list(globals().items()):
        if name.startswith("test_"):
            fn()
            print("ok ", name)
