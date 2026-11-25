"""Unit normalisation rule (pure). Values come from the real price sheet and usage export (see scripts/diag_units2.py)."""
from decimal import Decimal as D

import pytest

from azfinops.units import unit_parts


@pytest.mark.parametrize("unit,expected", [
    ("1 Hour", (D(1), "hour")), ("100 Hours", (D(100), "hour")), ("10000 Hours", (D(10000), "hour")),
    ("1/Month", (D(1), "/month")), ("1 /Month", (D(1), "/month")),
    ("10K", (D(10000), "")), ("1000000", (D(1000000), "")), ("10M", (D(10000000), "")),
    ("1 GB/Month", (D(1), "gb/month")), ("100 GB/Month", (D(100), "gb/month")), ("200 GB", (D(200), "gb")),
    ("1 TB", (D(1), "tb")), ("1 /Day", (D(1), "/day")),
    ("Each", (None, "each")), ("", (None, "")), (None, (None, "")),
])
def test_unit_parts(unit, expected):
    assert unit_parts(unit) == expected


def test_prices_per_base_unit_agree_for_real_examples():
    # Compute Pool CU: sheet 9.50 per 100 Hours, usage 0.095 per 1 Hour. Ingress: sheet 1.06 per 200 GB, usage 0.0053 per GB
    n_s, b_s = unit_parts("100 Hours"); n_u, b_u = unit_parts("1 Hour")
    assert b_s == b_u and D("9.50") / n_s == D("0.095") / n_u
    n_s, b_s = unit_parts("200 GB"); n_u, b_u = unit_parts("1 GB")
    assert b_s == b_u and D("1.06") / n_s == D("0.0053") / n_u


def test_conversions_between_different_base_words_are_deliberately_not_applied():
    assert unit_parts("1 TB")[1] != unit_parts("1 GB")[1]          # TB to GB is unverified: stays unmatched
    assert unit_parts("1 /Day")[1] != unit_parts("1/Month")[1]     # per day to per month is unverified
