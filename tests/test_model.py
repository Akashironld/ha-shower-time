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


def test_shower_head_flow_is_learned_from_the_shower_flow_only():
    """#10: the tank keeps falling after a shower and a tap used right after it falls in the same draw. Only the
    longest unbroken shower-like flow is the shower head: not the bursts and trickles after it."""
    rate = [(100, 6.6), (108, 0.0), (112, 2.8), (113, 0.0), (115, 4.2), (116, 0.0), (118, 1.5), (120, 0.0)]
    draw = model.Draw(start=99, minutes=24, top_start=55.0, bottom_start=50.0, bottom_low=30.0)
    assert [round(x, 1) for x in model.water_flow_samples(("rate", rate), [draw])] == [6.6]
    bursts = [(100, 4.8), (101, 1.5), (102, 6.4), (103, 1.2), (104, 4.9), (105, 0.0)]     # washing up
    assert model.water_flow_samples(("rate", bursts), [model.Draw(99, 10, 55.0, 50.0, 40.0)]) == []
    total = [(99, 1000.0)] + [(100 + i, 1000.0 + 7.0 * (i + 1)) for i in range(8)] + [(112, 1060.0), (113, 1063.0)]
    assert [round(x, 1) for x in model.water_flow_samples(("total", total), [draw])] == [7.0]


if __name__ == "__main__":
    for name, fn in list(globals().items()):
        if name.startswith("test_"):
            fn()
            print("ok ", name)


# --------------------------------------------------------------------------- layer model / sensor heights (#14)

def test_tank_types_give_the_spec_sheet_heights():
    g = model.new_geometry("weheat_wbl300", None, True)
    assert g["source"] == "weheat_wbl300" and g["learn"]
    assert 0.75 < g["top"] < 0.82 and 0.28 < g["bottom"] < 0.34      # 1220 / 560 mm of 120..1522 mm
    g2 = model.new_geometry("weheat_wbl200", None, False)
    assert 0.55 < g2["top"] < 0.62 and not g2["learn"]


def test_typed_heights_replace_the_tank_type_and_unknown_tank_waits():
    g = model.new_geometry("weheat_wbl200", {"outlet": 1500, "top": 1300, "bottom": 400, "inlet": None}, True)
    assert g["source"] == "manual" and g["top"] > 0.8
    assert model.new_geometry("other", None, True) is None           # unknown tank: the simple model, as before
    assert model.new_geometry("other", {"outlet": 1000, "top": 400, "bottom": 600}, True) is None   # upside down


def test_layer_litres_follow_the_hot_water():
    g = model.new_geometry("weheat_wbl300", None, True)
    kw = dict(cold=15.5, shower_temp=38.0, volume=300)
    full = model.layer_litres(g, 45.5, 45.4, hot=45.5, **kw)
    mid = model.layer_litres(g, 45.4, 22.0, hot=45.5, **kw)
    low = model.layer_litres(g, 30.0, 16.0, hot=45.5, **kw)
    assert 300 < full < 380                                          # 300 L at 45.5 makes ~400 L at 38 at most
    assert full > mid > low >= 0
    assert model.layer_litres(g, 36.0, 16.0, hot=36.0, **kw) == 0.0


def test_estimate_uses_the_layer_model_and_keeps_degrees_consistent():
    p = model.new_params("generic")
    plain = model.estimate(p, 45.6, 45.4, volume=300, shower_temp=38.0, month=11, head_flow=5.0)
    p["geo"] = model.new_geometry("weheat_wbl300", None, True)
    e = model.estimate(p, 45.6, 45.4, volume=300, shower_temp=38.0, month=11, head_flow=5.0)
    assert e.liters > plain.liters * 1.4                             # Pat28: 164 L was far too little
    assert abs(e.average - e.minutes * e.per_minute - e.empty_at) < 1e-6
    assert model.estimate(p, 45.6, None, volume=300, shower_temp=38.0, month=11).minutes == \
        model.estimate(model.new_params("generic"), 45.6, None, volume=300, shower_temp=38.0, month=11).minutes


def test_blur_grows_while_a_part_used_tank_stands_and_starts_again_when_full():
    assert model.spread(48.4, 24.8, 0.0, 0.0) == 0.0
    six = model.spread(48.4, 24.8, 0.0, 6 * 60)
    assert 0.6 < six < 0.65                                          # 1 - e^-1 after SPREAD_HOURS
    stepwise = 0.0
    for _ in range(72):                                              # the same six hours in 5-minute steps
        stepwise = model.spread(48.4, 24.8, stepwise, 5.0)
    assert abs(stepwise - six) < 1e-9
    assert model.spread(48.4, 24.8, 0.0, 14 * 60) > 0.9
    assert model.spread(57.0, 54.1, 0.9, 60) == 0.0                  # reheated: hot past the bottom sensor
    assert model.spread(48.4, None, 0.5, 60) == 0.0                  # one sensor: nothing to tell
    assert abs(model.spread(48.4, 24.8, 0.3, -5) - 0.3) < 1e-12      # a clock step back changes nothing
    # a restart of 8 hours with a full tank, and a shower running right after it: the tank did not stand part-used
    assert model.spread(52.0, 30.0, 0.0, 8 * 60, prev_share=0.0) == 0.0
    assert model.spread(48.4, 24.8, 0.0, 8 * 60, prev_share=1.0) == model.spread(48.4, 24.8, 0.0, 8 * 60)


def test_a_part_used_tank_that_stood_overnight_has_less_than_the_layer_model_says():
    # #10: 48.4/24.8 °C after a bath the day before and a night standing; the water ran cold after ~10 minutes
    p = model.new_params("generic")
    simple = model.estimate(p, 48.4, 24.8, volume=200, shower_temp=38.0, month=11).minutes
    p["geo"] = model.new_geometry("weheat_wbl200", None, True)
    kw = dict(volume=200, shower_temp=38.0, month=11, hot=50.5)
    fresh = model.estimate(p, 48.4, 24.8, **kw)
    night = model.estimate(p, 48.4, 24.8, blur=model.spread(48.4, 24.8, 0.0, 14 * 60), **kw)
    assert fresh.minutes > 2 * simple                                # right after a shower: the layer model
    assert abs(night.minutes - simple) < 0.1 * (fresh.minutes - simple)
    assert abs(night.average - night.minutes * night.per_minute - night.empty_at) < 1e-6
    full = model.estimate(p, 55.0, 54.0, volume=200, shower_temp=38.0, month=11, hot=55.0)
    assert model.estimate(p, 55.0, 54.0, volume=200, shower_temp=38.0, month=11, hot=55.0, blur=1.0).minutes == \
        full.minutes                                                 # a full tank keeps the layer model


def test_layer_litres_formula_matches_a_sum_over_the_column():
    g = model.new_geometry("weheat_wbl200", None, True)
    for top, bottom, hot in ((52.0, 51.5, 52.0), (51.0, 30.0, 52.0), (44.0, 18.0, 52.0)):
        z0 = model.fit_thermocline(top, bottom, hot, 15.0, g["top"], g["bottom"])
        n, zi = 4000, g["inlet"]
        temps = [model.layer_temp(zi + (1 - zi) * (i + 0.5) / n, hot, 15.0, z0) for i in range(n)]
        summed = sum((t - 15.0) / 23.0 for t in temps if t > 38.0) * 200 * (1 - zi) / n
        exact = model.layer_litres(g, top, bottom, hot=hot, cold=15.0, shower_temp=38.0, volume=200)
        assert abs(exact - summed) < 1.0


def test_a_timer_reheat_with_a_hot_top_does_not_raise_phi():
    """#14: a reheat at 13:00 while the top is still 42.7 is not the tank running empty."""
    p = model.new_params("generic")
    p["phi"] = {"v": 0.5, "n": 5}
    rows, m = [], 0
    for day in range(3):
        for i in range(1440):
            heating = 780 <= i < 840
            top = 42.7 if not heating else 42.7 + (i - 780) * 0.05
            bottom = 22.7 if not heating else 22.7 + (i - 780) * 0.4
            if i >= 840:
                top, bottom = 45.6, 45.4
            rows.append(model.Row(m, top, bottom, heating))
            m += 1
    res, _ = model.learn(p, rows, volume=300, shower_temp=38.0, since=0, energy_by_hour=None,
                         previous_cycle=None, month_of=lambda t: 11)
    assert res.params["phi"]["v"] <= 0.5


def _shower_rows(g, true_top, true_bottom, hot=50.0, cold=15.0, flow=6.0, volume=200):
    """Readings before and after showers of different depths, made with the layer model at the true heights."""
    draws = []
    for start_z0, minutes in ((0.35, 10), (0.45, 12), (0.50, 10), (0.55, 14), (0.40, 15), (0.60, 9)):
        z1 = start_z0 + flow * minutes * (38 - cold) / (hot - cold) / volume
        r = lambda z0: [round(model.layer_temp(z, hot, cold, z0), 1) for z in (true_top, true_bottom)]
        draws.append(r(start_z0) + r(z1) + [hot, cold, minutes, flow, 38.0])
    return draws


def test_sensor_heights_are_learned_from_deep_showers():
    g = model.new_geometry("weheat_wbl200", None, True)               # spec says top at ~0.58
    g["draws"] = _shower_rows(g, true_top=0.74, true_bottom=g["bottom"])
    learned = model.learn_geometry(g, 200)
    assert learned["n"] >= model.GEO_MIN_DRAWS
    assert abs(learned["top"] - 0.74) < 0.05
    fixed = model.learn_geometry({**g, "learn": False}, 200)
    assert fixed["top"] == g["top"]                                   # learning off: heights stay


def test_too_few_deep_showers_keep_the_heights():
    g = model.new_geometry("weheat_wbl200", None, True)
    g["draws"] = _shower_rows(g, true_top=0.74, true_bottom=g["bottom"])[:2]
    assert model.learn_geometry(g, 200)["top"] == g["top"]
