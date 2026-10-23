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

    def heat_lower(self, kwh):
        """Partial top-up: the coil warms the lower half only, so the tank stays layered."""
        half = self.n // 2
        avg = sum(self.t[:half]) / half
        dk = kwh / (CP * self.v / 2)
        self.t[:half] = [min(avg + dk, self.t[-1])] * half
        return kwh

    def lose(self, k_per_min):
        self.t = [x - k_per_min for x in self.t]


def simulate(days=21, flow=6.0, seed=1, cold=15.0, shower_temp=38.0, water=None, smart=False):
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
            target = rnd.choice([50.0, 56.0, 65.0]) if smart else 55.0
        if smart and m % 1440 in (9 * 60, 17 * 60) and rnd.random() < 0.6 and not heating_until:
            for k in range(12):                         # smart controller: a short top-up of the lower half
                e = tank.heat_lower(0.08)
                energy[(minute + k) // 60 * 60] = energy.get((minute + k) // 60 * 60, 0.0) + e
            rows.append(model.Row(minute=minute, top=round(tank.sensors()[0], 1), bottom=round(tank.sensors()[1], 1), heating=True))
            continue
        if heating_until and minute <= heating_until:
            e = tank.heat(target)
            if e > 0:
                heating = True
                energy[minute // 60 * 60] = energy.get(minute // 60 * 60, 0.0) + e
            else:
                heating_until = None
        if m in plan:
            busy_until = m + plan[m]
        use = 0.0
        if m < busy_until and not heating:
            tank.shower(flow, shower_temp)
            use = flow
        if water is not None:
            if rnd.random() < 0.004:
                use += 9.0                              # toilet flush or a tap somewhere else
            water.append((minute, round(use + rnd.uniform(-0.2, 0.2), 2) if use else 0.0))
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


def test_senses_shower_temperature_from_known_head_flow():
    """Shower at 40 °C, learn in auto mode (38 °C reference), then a known 6 L/min head gives ≈ 40 °C."""
    rows, energy, tank = simulate(flow=6.0, shower_temp=40.0, seed=5, days=28)
    p = model.new_params("generic")
    for k in p["cold"]:
        p["cold"][k] = 15.0
    res, _ = model.learn(p, rows, volume=200, shower_temp=38, since=0, energy_by_hour=energy,
                         previous_cycle=None, month_of=month_of)
    e = model.estimate(res.params, 55.0, 55.0, volume=200, shower_temp=38, month=10, head_flow=6.0)
    assert e.shower_temp_sensed
    assert 37.5 <= e.shower_temp <= 43.0, (e.shower_temp, res.params["flow"])
    # minutes do not depend on the head flow, litres do
    e2 = model.estimate(res.params, 55.0, 55.0, volume=200, shower_temp=38, month=10)
    assert abs(e.minutes - e2.minutes) < 1e-9 and abs(e.liters - e.minutes * 6.0) < 1e-9


def test_water_meter_learns_head_flow():
    water = []
    rows, energy, _ = simulate(flow=7.2, seed=7, water=water)
    p = model.new_params("generic")
    for k in p["cold"]:
        p["cold"][k] = 15.0
    res, _ = model.learn(p, rows, volume=200, shower_temp=38, since=0, energy_by_hour=energy,
                         previous_cycle=None, month_of=month_of, water=("rate", water))
    hf = res.params["head_flow"]
    assert hf["n"] >= 10, hf
    assert 6.6 <= hf["v"] <= 7.8, hf
    # the same from a cumulative meter (litres)
    total, acc = [], 0.0
    for m, r in water:
        acc += max(r, 0.0)
        total.append((m, round(acc, 1)))
    res2, _ = model.learn(model.new_params("generic"), rows, volume=200, shower_temp=38, since=0, energy_by_hour=energy,
                          previous_cycle=None, month_of=month_of, water=("total", total))
    assert 6.6 <= res2.params["head_flow"]["v"] <= 7.8, res2.params["head_flow"]


def test_smart_controller_varying_targets_and_top_ups():
    """Reheats to 50/56/65 °C and partial midday top-ups (tank left layered) must not bias the learned flow.

    The heat of a top-up is not in the sum between two full reheats, so such intervals are skipped;
    without that the flow came out ~15 % low. Compared with a fixed-schedule tank, same seed."""
    out = []
    for smart in (False, True):
        rows, energy, _ = simulate(flow=6.0, seed=11, days=28, smart=smart)
        p = model.new_params("generic")
        for k in p["cold"]:
            p["cold"][k] = 15.0
        p["flow"] = {"v": 7.5, "n": 0}                   # no prior: look at what the data says
        p["veff_factor"] = {"v": 1.0, "n": 20}           # heat meter scale known
        res, _ = model.learn(p, rows, volume=200, shower_temp=38, since=0, energy_by_hour=energy,
                             previous_cycle=None, month_of=month_of)
        assert res.samples["flow"] >= 4, res.samples
        out.append(res.params["flow"]["v"])
    assert abs(out[1] - out[0]) < 0.5, out
    assert 5.0 <= out[1] <= 7.0, out


def test_cold_water_from_outdoor_average():
    """cold = a + b * 3-week outdoor average: the profile's line, only lowered by colder samples; the table is
    smooth."""
    prior = {"a": {"v": 9.0, "n": 10}, "b": {"v": 0.6, "n": 10}}
    # the bottom after a shower, far above the mains water (the samples behind 25 °C in October, #10)
    warm = [[16.9, 32.3], [16.79, 32.3], [16.8, 20.3], [16.61, 35.9], [16.14, 36.9], [15.85, 26.2]]
    assert model.fit_cold_model(prior, warm) == prior
    one = warm + [[16.0, 16.6]]                          # one reading below the line (a = 7.0) does not move it
    assert model.fit_cold_model(prior, one)["a"]["v"] == 9.0
    two = one + [[15.0, 16.2]]                           # a second one does: a = 7.2, the higher of the two
    cm = model.fit_cold_model(prior, two)
    assert abs(cm["a"]["v"] - 7.2) < 0.01 and cm["b"]["v"] == 0.6, cm
    assert model.fit_cold_model(prior, two) == cm        # kept samples count once, however often it is fitted
    low = [[10.0, 6.0], [10.0, 6.5]]                     # a sensor reading low: at most 4 °C below the profile
    assert model.fit_cold_model(prior, low)["a"]["v"] == 5.0
    p = model.new_params("generic")
    assert abs(model.cold_for(p, 10, 286, 17.2) - (9.0 + 0.6 * 17.2)) < 0.01
    # no jump at a month boundary
    d1, d2 = model.cold_for(p, 10, 304), model.cold_for(p, 11, 305)
    assert abs(d1 - d2) < 0.3, (d1, d2)


def learn_nightly(p, rows, energy, ema):
    """Learn night after night, as the integration does."""
    start = rows[0].minute
    for night in range(1, (rows[-1].minute - start) // 1440 + 1):
        end = start + night * 1440
        res, _ = model.learn(p, [r for r in rows if r.minute < end], volume=200, shower_temp=38, since=end - 1440,
                             energy_by_hour=energy, previous_cycle=None, month_of=month_of, ema_of=lambda m: ema)
        p = res.params
    return p


def test_bottom_after_a_shower_does_not_warm_the_cold_water():
    """#10: after most showers the bottom stays well above the mains water (here up to 38 °C with 15 °C mains).
    Taken for the mains itself, that pushed the cold water to 25 °C within two weeks."""
    rows, energy, _ = simulate(days=14, cold=15.0)
    p = learn_nightly(model.new_params("generic"), rows, energy, ema=10.0)    # the generic line: 9 + 0.6 * 10
    assert len(p["cold_samples"]) >= 10
    assert p["cold_model"]["a"]["v"] == 9.0 and model.cold_for(p, 10, None, 10.0) == 15.0, p["cold_model"]
    assert p["cold"]["10"] == model.COLD_GENERIC["10"]


def test_colder_mains_water_lowers_the_cold_water():
    """Mains water colder than the profile: the coldest bottom readings pull the line and the month down."""
    rows, energy, _ = simulate(days=14, cold=11.0)
    p = learn_nightly(model.new_params("generic"), rows, energy, ema=10.0)    # the line says 15
    assert 11.0 <= model.cold_for(p, 10, None, 10.0) <= 12.5, p["cold_model"]
    assert 11.0 <= p["cold"]["10"] < model.COLD_GENERIC["10"] - 2, p["cold"]


def test_cold_water_learned_too_warm_is_repaired():
    """Parameters saved by 0.13.1 and earlier: months pushed above the profile go back, the line is refitted from
    the kept samples without counting them again every night (a 7.24 had become 13.09 from six samples)."""
    p = model.new_params("weheat_200")
    del p["cold_version"]
    p["cold"]["10"], p["cold"]["01"] = 25.0, 8.5                 # pushed up / learned below the profile
    p["cold_model"] = {"a": {"v": 13.087, "n": 40}, "b": {"v": 0.604, "n": 40}}
    p["cold_samples"] = [[16.9, 32.3], [16.79, 32.3], [16.8, 20.3], [16.61, 35.9], [16.14, 36.9], [15.85, 26.2]]
    q = model.ensure_params(p)
    assert q["cold"]["10"] == 19.7 and q["cold"]["01"] == 8.5
    assert q["cold_model"] == model.PROFILES["weheat_200"]["cold_model"] and q["cold_version"] == 2
    assert len(q["cold_samples"]) == 6
    q["cold"]["10"] = 18.0                                       # learned the new way: left alone from now on
    assert model.ensure_params(q)["cold"]["10"] == 18.0


if __name__ == "__main__":
    for name, fn in list(globals().items()):
        if name.startswith("test_"):
            fn()
            print("ok ", name)
