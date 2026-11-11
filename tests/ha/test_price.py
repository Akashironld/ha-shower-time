"""Price units: cents and MWh, in the live price and the backfill alike."""
from __future__ import annotations

from types import SimpleNamespace

import pytest

from custom_components.shower_time.ledger import parse_price, unit_factor


def st(value, unit=None):
    return SimpleNamespace(state=value, attributes={"unit_of_measurement": unit} if unit else {})


@pytest.mark.parametrize("unit,factor,cur", [
    ("ct/kWh", 0.01, "€"), ("c/kWh", 0.01, "€"), ("cent/kWh", 0.01, "€"), ("¢/kWh", 0.01, "€"),
    ("EUR/kWh", 1.0, "€"), ("€/kWh", 1.0, "€"), ("$/kWh", 1.0, "$"),
    ("€/MWh", 0.001, "€"), ("EUR/MWh", 0.001, "€"), ("ct/MWh", 0.00001, "€"),
    (None, 1.0, ""), ("", 1.0, ""),
])
def test_unit_factor(unit, factor, cur) -> None:
    f, c = unit_factor(unit)
    assert f == pytest.approx(factor) and c == cur


@pytest.mark.parametrize("value,unit,price,cur", [
    ("25", "ct/kWh", 0.25, "€"), ("0.25", "EUR/kWh", 0.25, "€"), ("250", "€/MWh", 0.25, "€"),
    ("250", "EUR/MWh", 0.25, "€"), ("0.3", None, 0.3, ""),
])
def test_parse_price(value, unit, price, cur) -> None:
    p, c = parse_price(st(value, unit))
    assert p == pytest.approx(price) and c == cur


@pytest.mark.parametrize("state", [None, st("unavailable", "EUR/kWh"), st("unknown"), st("abc", "ct/kWh")])
def test_parse_price_without_a_number(state) -> None:
    assert parse_price(state) == (None, "")
