"""Billing unit normalisation, shared by the price sheet comparison and its tests.

The price sheet quotes a price per block ('100 Hours', '1000000', '10K') while usage rows quote the price per one
unit. A unit string is split into a block size and a base word, and prices are compared per one base unit.
Only the forms below are normalised: spaces, case, 'hours' read as 'hour', a leading block number, and K / M
suffixes. Conversions between different base words (TB to GB, per day to per month) are NOT applied: they were
not verified against the data, so those units stay unmatched rather than guessed.
"""
from __future__ import annotations

import re
from decimal import Decimal

_ONLY_BLOCK = re.compile(r"(\d+)([KkMm]?)")
_LEADING = re.compile(r"(\d+)\s*(.*)", re.DOTALL)


def _base(text: str) -> str:
    return text.lower().replace(" ", "").replace("hours", "hour")


def unit_parts(unit: str | None) -> tuple[Decimal | None, str]:
    """(block size, base word); block size is None when the unit has no leading number."""
    u = unit or ""
    m = _ONLY_BLOCK.fullmatch(u)
    if m:
        mult = {"": 1, "k": 1000, "m": 1_000_000}[m.group(2).lower()]
        return Decimal(m.group(1)) * mult, ""
    m = _LEADING.match(u)
    if m:
        return Decimal(m.group(1)), _base(m.group(2))
    return None, _base(u)


def unit_sql(col: str) -> tuple[str, str]:
    """The same rule as SQL for a column: (block size DOUBLE, base word)."""
    n = (f"(TRY_CAST(regexp_extract({col}, '^([0-9]+)', 1) AS DOUBLE) * CASE "
         f"WHEN regexp_matches({col}, '^[0-9]+[Kk]$') THEN 1000 WHEN regexp_matches({col}, '^[0-9]+[Mm]$') THEN 1000000 "
         f"ELSE 1 END)")
    base = (f"replace(replace(lower(regexp_replace(regexp_replace({col}, '^[0-9]+[KkMm]?$', ''), "
            f"'^[0-9]+\\s*', '')), ' ', ''), 'hours', 'hour')")
    return n, base
