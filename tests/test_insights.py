"""Tests for insights.py: shower habits, today's forecast and heat-loss / leak watching."""
from __future__ import annotations

import os
import sys
from datetime import datetime, timedelta, timezone

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "custom_components", "shower_time"))

import insights  # noqa: E402
import model  # noqa: E402

UTC = timezone.utc


def local_of(minute: int) -> datetime:
    return datetime.fromtimestamp(minute * 60, UTC)          # tests run in UTC "local" time


# --------------------------------------------------------------------------- habits

def test_minutes_by_hour_spreads_a_shower_over_the_hours_it_covers():
    out = insights.minutes_by_hour([(21.5, 10), (21.9, 12)])     # 21:30-21:40 and 21:54-22:06
    assert round(out[21], 2) == round(10 + 6, 2)
    assert round(out[22], 2) == 6
    assert sum(out) == 22


def evening_habits(days: int, start_hour: float = 21.5, minutes: float = 15) -> dict:
    h = insights.new_habits()
    day = datetime(2026, 9, 1)
    for i in range(days):
        d = day + timedelta(days=i)
        insights.add_day(h, d.weekday(), d.date().isoformat(), insights.minutes_by_hour([(start_hour, minutes)]))
    return h


def test_habits_need_a_week():
    assert not insights.habits_ready(evening_habits(6))
    assert insights.habits_ready(evening_habits(7))


def test_expected_follows_the_habit_and_weekdays_take_over():
    h = evening_habits(28)
    exp = insights.expected_by_hour(h, 2)
    assert abs(exp[21] - 15) < 0.01 and sum(exp) - exp[21] < 0.01
    # Saturdays (weekday 5) change to a morning shower: the Saturday profile follows within a few weeks,
    # other days stay in the evening
    sat = datetime(2026, 9, 26)
    for i in range(4):
        d = sat + timedelta(days=7 * i)
        insights.add_day(h, 5, d.date().isoformat(), insights.minutes_by_hour([(9.0, 15)]))
    sat_now = insights.expected_by_hour(h, 5)
    assert sat_now[9] > 5                                          # about half way after four Saturdays
    assert insights.expected_by_hour(h, 2)[21] > 13               # the other days hardly change


# --------------------------------------------------------------------------- forecast

def test_forecast_enough_and_not_enough():
    h = evening_habits(14)
    wd = datetime(2026, 9, 15).weekday()
    ok = insights.forecast(h, weekday=wd, hour=18.0, minutes_now=40, per_minute=0.15, loss_k_h=0.27, typical=10)
    assert ok.enough and ok.runs_out_at is None and abs(ok.expected_minutes - 15) < 0.1
    assert abs(ok.next_shower_at - 3.0) < 0.01                   # 21:00 hour starts in 3 h
    short = insights.forecast(h, weekday=wd, hour=18.0, minutes_now=12, per_minute=0.15, loss_k_h=0.27, typical=10)
    assert not short.enough
    assert 21.0 <= short.runs_out_at < 22.0                       # during the usual evening shower
    after = insights.forecast(h, weekday=wd, hour=22.5, minutes_now=3, per_minute=0.15, loss_k_h=0.27, typical=10)
    assert after.enough and after.expected_minutes == 0           # the showers of today are done


def test_forecast_counts_standing_loss():
    h = evening_habits(14)
    wd = datetime(2026, 9, 15).weekday()
    # 16 minutes now covers 15 minutes at 21:00 only without loss; 0.27 K/h at 0.15 K/min = 1.8 min/h lost
    with_loss = insights.forecast(h, weekday=wd, hour=8.0, minutes_now=16, per_minute=0.15, loss_k_h=0.27, typical=10)
    no_loss = insights.forecast(h, weekday=wd, hour=8.0, minutes_now=16, per_minute=0.15, loss_k_h=0.0, typical=10)
    assert no_loss.enough and not with_loss.enough


# --------------------------------------------------------------------------- heat loss and leaks

def cooling_rows(start_minute: int, hours: float, top0: float, bottom0: float, ua: float, volume: float = 200,
                 leak_l_h: float = 0.0, room: float = 20.0) -> list[model.Row]:
    """An evenly hot tank cooling through its insulation (UA W/K), optionally with a hot tap dripping
    (cold water of 12 °C entering at the bottom, cooling the bottom layer)."""
    rows, top, bot = [], top0, bottom0
    for i in range(int(hours * 60)):
        rows.append(model.Row(minute=start_minute + i, top=round(top, 1), bottom=round(bot, 1), heating=False))
        mean = (top + bot) / 2
        k = ua / (volume * model.CP * 1000) / 60                  # 1/min
        top -= k * (top - room)
        bot -= k * (bot - room)
        if leak_l_h:
            bot -= leak_l_h / 60 * (bot - 12) / (volume * 0.15)   # the cold water mixes into the bottom 15 %
        _ = mean
    return rows


def test_loss_coefficient_is_recovered_and_independent_of_tank_temperature():
    m0 = int(datetime(2026, 10, 1, 1, 0, tzinfo=UTC).timestamp() // 60)
    hot = insights.loss_samples(cooling_rows(m0, 6, 64, 63.5, ua=2.0), volume=200, local_of=local_of)
    m1 = int(datetime(2026, 10, 2, 1, 0, tzinfo=UTC).timestamp() // 60)
    warm = insights.loss_samples(cooling_rows(m1, 6, 50, 49, ua=2.0), volume=200, local_of=local_of)
    assert len(hot) == 1 and len(warm) == 1
    assert abs(hot[0].ua - 2.0) < 0.15 and abs(warm[0].ua - 2.0) < 0.2
    assert hot[0].rate > warm[0].rate                             # hotter tank loses faster, same coefficient


def test_taps_reheats_and_uneven_tanks_are_not_measured():
    m0 = int(datetime(2026, 10, 1, 0, 0, tzinfo=UTC).timestamp() // 60)
    rows = cooling_rows(m0, 8, 60, 59.5, ua=2.0)
    for r in rows[150:153]:                                        # a hand wash at 02:30
        r.bottom -= 1.0
    for r in rows[153:]:
        r.bottom -= 1.0
    s = insights.quiet_stretches(rows)
    assert all(not (seg[0].minute <= m0 + 150 + 60 <= seg[-1].minute) for seg in s)   # settles for an hour after
    uneven = cooling_rows(m0, 8, 60, 30, ua=2.0)
    assert insights.loss_samples(uneven, volume=200, local_of=local_of) == []
    heating = cooling_rows(m0, 8, 60, 59.5, ua=2.0)
    for r in heating:
        r.heating = True
    assert insights.loss_samples(heating, volume=200, local_of=local_of) == []


def sample(day: int, ua: float, top_rate: float = 0.30, bottom_rate: float = 0.29) -> dict:
    return {"day": "2026-10-%02d" % day, "hours": 6.0, "average": 58.0, "rate": 0.3, "top_rate": top_rate,
            "bottom_rate": bottom_rate, "ua": ua}


def test_loss_status_normal_noise_is_not_an_alarm():
    noisy = [1.78, 1.90, 1.94, 2.07, 1.99, 2.07, 1.85, 2.01, 1.92, 2.05, 1.97, 2.10, 1.88, 2.06]   # ±7 % like a real tank
    st = insights.loss_status([sample(i + 1, u) for i, u in enumerate(noisy)])
    assert st["ready"] and not st["raised"] and not st["leak_like"]


def test_loss_status_raised_insulation_and_leak():
    base = [sample(i + 1, 2.0) for i in range(10)]
    worse = base + [sample(11 + i, 2.9) for i in range(4)]
    st = insights.loss_status(worse)
    assert st["raised"] and not st["leak_like"] and st["ratio"] > 1.4
    leak = base + [sample(11 + i, 2.9, top_rate=0.30, bottom_rate=0.75) for i in range(4)]
    st = insights.loss_status(leak)
    assert st["raised"] and st["leak_like"]
    one_bad_night = base + [sample(11, 2.0), sample(12, 3.0), sample(13, 2.0), sample(14, 2.0)]
    assert not insights.loss_status(one_bad_night)["raised"]


def test_slow_drip_shows_in_the_cooling_pattern():
    m = int(datetime(2026, 10, 1, 1, 0, tzinfo=UTC).timestamp() // 60)
    samples = []
    for d in range(14):
        leak = 0.5 if d >= 10 else 0.0                             # a hot tap starts dripping 0.5 L/h on day 11
        rows = cooling_rows(m + d * 1440, 6, 62, 61.6, ua=1.9, leak_l_h=leak)
        samples += insights.loss_samples(rows, volume=200, local_of=local_of)
    st = insights.loss_status(insights.merge_samples([], samples))
    assert st["ready"] and st["leak_like"]
    assert st["bottom_share_recent"] > 2 * st["bottom_share_baseline"]


def test_drip_at_night_is_seen_from_the_first_nights():
    m = int(datetime(2026, 10, 1, 0, 0, tzinfo=UTC).timestamp() // 60)
    healthy, dripping = [], []
    for d in range(4):
        healthy += insights.night_drips(cooling_rows(m + d * 1440, 6, 62, 61.6, ua=1.9), local_of=local_of)
        dripping += insights.night_drips(cooling_rows(m + d * 1440, 6, 62, 61.6, ua=1.9, leak_l_h=5.0), local_of=local_of)
    assert not insights.drip_status(insights.merge_drips([], healthy))
    assert insights.drip_status(insights.merge_drips([], dripping))


def test_a_tap_at_night_is_not_a_drip():
    m = int(datetime(2026, 10, 1, 0, 0, tzinfo=UTC).timestamp() // 60)
    nights = []
    for d in range(4):
        rows = cooling_rows(m + d * 1440, 6, 62, 61.6, ua=1.9)
        for r in rows[150:]:                                        # 02:30: someone washes their hands, -2 K at once
            r.bottom -= 2.0
        nights += insights.night_drips(rows, local_of=local_of)
    assert not insights.drip_status(insights.merge_drips([], nights))


def test_real_tank_measurements():
    """Six days of a real Weheat 200 L tank (Oct 2026): consistent coefficient, top and bottom cool together."""
    real = [(1.783, 0.29, 0.251), (1.903, 0.299, 0.268), (1.936, 0.316, 0.273), (2.067, 0.385, 0.382),
            (1.991, 0.256, 0.272), (2.072, 0.371, 0.371)]
    samples = [sample(i + 1, ua, t, b) for i, (ua, t, b) in enumerate(real)]
    st = insights.loss_status(samples)
    assert not st["ready"] and abs(st["ua_recent"] - 2.0) < 0.1
    assert insights.heat_loss_watts(st["ua_recent"], 55) == round(st["ua_recent"] * 35, 1)


def test_merge_keeps_the_longest_stretch_per_day():
    a = insights.LossSample(day="2026-10-01", hours=8.0, average=60, rate=0.3, top_rate=0.3, bottom_rate=0.3, ua=2.0)
    b = insights.LossSample(day="2026-10-01", hours=3.5, average=60, rate=0.4, top_rate=0.4, bottom_rate=0.4, ua=2.6)
    merged = insights.merge_samples(insights.merge_samples([], [a]), [b])
    assert len(merged) == 1 and merged[0]["ua"] == 2.0


# --------------------------------------------------------------------------- what kind of hot water use

def run_use(steps, bath=lambda t: False, start=(55.0, 50.0), cold=15.0, confirm_k=insights.SHOWER_CONFIRM_K):
    """steps: [(minute, top, bottom)] after the start. Returns the session and the minute it became a shower."""
    p = model.new_params("weheat_200")
    s = insights.UseSession(start=0.0, start_avg=model.tank_average(p, *start), start_top=start[0], start_bottom=start[1])
    confirmed = None
    for t, top, bottom in steps:
        if s.update(t, model.tank_average(p, top, bottom), bath(t), confirm_k, top=top, bottom=bottom, cold=cold):
            confirmed = t
    return s, confirmed


def test_shower_is_confirmed_by_its_heat():
    # a shower: cold water comes in at the bottom, the top stays hot
    steps = [(2 * i, 55.0, 50.0 - 2.5 * i) for i in range(1, 8)]
    s, at = run_use(steps)
    assert s.kind == "shower" and at is not None and at <= 6
    assert s.final(14, 40) == "shower"


def test_washing_up_stays_a_tap():
    s, at = run_use([(1, 55.0, 49.6), (2, 55.0, 49.5), (3, 55.0, 49.5)])
    assert at is None and s.kind == "unknown" and s.final(3, 40) == "tap"


def test_bathroom_signal_confirms_quickly():
    steps = [(2 * i, 55.0, 50.0 - 1.0 * i) for i in range(1, 6)]
    without, at1 = run_use(steps)
    with_bath, at2 = run_use(steps, bath=lambda t: True)
    assert at2 is not None and (at1 is None or at2 < at1)
    assert with_bath.bath_seen


def test_hot_fill_is_not_a_shower():
    s, _ = run_use([(1, 55.0, 44.0), (2, 55.0, 38.0), (3, 55.0, 32.0)])     # bottom falls far faster than a shower
    assert s.kind == "fill" and s.final(3, 40) == "fill"


def test_heat_leaving_without_water_is_not_a_use():
    # a heat pump starting a reheat: the top dips, the bottom hardly moves
    s, _ = run_use([(2, 41.0, 21.5), (4, 37.0, 20.5), (6, 33.0, 19.5)], start=(43.5, 21.7), cold=17.0)
    assert s.kind == "coil" and s.final(6, 40) == "coil"
    # heat taken out through the coil: top and bottom fall while the bottom is still far above cold water
    s, _ = run_use([(2 * i, 55.8 - 0.35 * i, 49.4 - 1.5 * i) for i in range(1, 8)], start=(55.8, 49.4), cold=17.0)
    assert s.kind == "coil"


def test_end_of_a_long_shower_is_still_a_shower():
    # near the end of a long shower the top falls too, but the bottom is down at cold water by then
    steps = [(2 * i, 55.0, 50.0 - 4 * i) for i in range(1, 8)] + [(16, 52.0, 20.0), (18, 50.0, 19.0)]
    s, _ = run_use(steps, cold=17.0)
    assert s.kind == "shower"


def test_confirm_threshold_is_learned_between_taps_and_showers():
    assert insights.learn_confirm_k([7, 6, 12], [0.3]) == insights.SHOWER_CONFIRM_K       # too few yet
    k = insights.learn_confirm_k([3.3, 6.5, 12.4, 6.5, 12.8, 3.2, 6.6, 5.5], [0.25, 0.76, 0.3, 0.24, 0.34, 0.3])
    assert 1.0 <= k < 1.8                                                                 # faster than default
    assert insights.learn_confirm_k([1.0] * 6, [1.2] * 6) == insights.SHOWER_CONFIRM_K   # overlap: keep default


def test_bath_signal_humidity_power_and_switch():
    h = insights.BathSignal("humidity")
    h.feed(0, 60.0)
    h.feed(30, 60.5)
    assert not h.active(30)
    h.feed(32, 64.0)                                    # +4 %RH within 15 minutes (60.5 was in effect)
    assert h.active(33) and not h.active(50)
    fan = insights.BathSignal("power")
    for i in range(30):
        fan.feed(i * 40, 3.0 if i % 2 else 4.0)        # idles at 3-4 W
    assert not fan.active(1200)
    fan.feed(1201, 6.0)                                 # runs harder: someone is in the bathroom
    assert fan.active(1205)
    sw = insights.BathSignal("on_off")
    sw.feed(10, False)
    assert not sw.active(10)
    sw.feed(11, True)
    assert sw.active(15) and not sw.active(30)


def test_history_draws_are_judged_the_same_way():
    p = model.new_params("weheat_200")
    m0 = 1000
    shower = [model.Row(minute=m0 + i, top=55.0, bottom=50.0 - min(i, 12) * 2.0, heating=False) for i in range(25)]
    assert insights.draw_kind(p, shower, m0, 12, cold=15.0) == "shower"
    tap = [model.Row(minute=m0 + i, top=55.0, bottom=50.0 - (0.5 if i > 2 else 0), heating=False) for i in range(20)]
    assert insights.draw_kind(p, tap, m0, 4, cold=15.0) == "tap"
    coil = [model.Row(minute=m0 + i, top=55.8 - 0.2 * i, bottom=49.4 - 0.9 * i, heating=False) for i in range(20)]
    assert insights.draw_kind(p, coil, m0, 15, cold=17.0) == "coil"


def test_reheat_starting_during_a_shower_does_not_make_it_coil():
    """A real case: the heat pump started reheating 7 minutes into a shower; its start-up dips the top."""
    p = model.new_params("weheat_200")
    m0 = 2000
    vals = [(50.0, 47.3, 0), (50.0, 46.3, 0), (50.0, 46.3, 0), (49.9, 44.4, 0), (49.9, 44.4, 0), (49.9, 41.1, 0),
            (49.9, 41.1, 0), (49.7, 37.0, 1), (49.7, 37.0, 1), (49.3, 36.4, 1), (49.3, 36.4, 1), (49.1, 37.3, 1),
            (48.9, 38.4, 1), (48.8, 39.3, 1)]
    rows = [model.Row(minute=m0 + i, top=t, bottom=b, heating=bool(h)) for i, (t, b, h) in enumerate(vals)]
    assert insights.draw_kind(p, rows, m0, 5, cold=17.0) == "shower"


def test_learning_uses_only_showers_when_told_the_kind():
    """Heat that left without water (or washing up) must not teach the shower length."""
    p = model.new_params("generic")
    m0 = 5000
    rows = [model.Row(minute=m0 + i, top=55.0, bottom=50.0, heating=False) for i in range(60)]
    rows += [model.Row(minute=m0 + 60 + i, top=55.0, bottom=50.0 - 2.0 * min(i, 10), heating=False) for i in range(40)]
    rows += [model.Row(minute=m0 + 100 + i, top=55.0, bottom=30.0, heating=False) for i in range(60)]
    month_of = lambda m: 10
    plain, _ = model.learn(p, [model.Row(**vars(r)) for r in rows], volume=200, shower_temp=38, since=0,
                           energy_by_hour=None, previous_cycle=None, month_of=month_of)
    told, _ = model.learn(p, [model.Row(**vars(r)) for r in rows], volume=200, shower_temp=38, since=0,
                          energy_by_hour=None, previous_cycle=None, month_of=month_of, draw_kind=lambda d: "coil")
    assert plain.samples["duration"] == 1 and told.samples["duration"] == 0
