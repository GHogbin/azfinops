"""Financial calculations over canonical rows (see docs/methodology.md).

Rows are mappings with Decimal-coercible values and at least: cost_kind, billing_currency,
charge_type, cost. All functions:
  * refuse to mix cost_kind values (actual vs amortized) -> MixedBasisError,
  * keep native currencies separate (a currency argument is always required),
  * return None, never zero, when a denominator is zero or an input is missing,
  * round only in `rnd` (presentation); internal arithmetic is exact Decimal.
"""
from __future__ import annotations

from collections.abc import Iterable, Mapping
from dataclasses import dataclass, field
from datetime import date
from decimal import ROUND_HALF_EVEN, Decimal, localcontext
from typing import Any

Row = Mapping[str, Any]
ZERO = Decimal(0)


class MixedBasisError(ValueError):
    """Raised when rows from both actual and amortized exports are combined."""


class MissingRateError(LookupError):
    """Raised when no dated FX rate exists; a missing rate is never treated as 1 or 0."""


def d(x: Any) -> Decimal:
    if isinstance(x, Decimal):
        return x
    return Decimal(str(x).strip())


def sum_decimal(values: Iterable[Decimal]) -> Decimal:
    """Keep the full precision of SQL DECIMAL(38,18) values while accumulating."""
    with localcontext() as ctx:
        ctx.prec = 80
        return sum(values, ZERO)


def rnd(x: Decimal, places: int = 2) -> Decimal:
    """Presentation rounding (banker's rounding). Never applied inside calculations."""
    return d(x).quantize(Decimal(1).scaleb(-places), rounding=ROUND_HALF_EVEN)


def _has(v: Any) -> bool:
    return v is not None and str(v).strip() != ""


def _single_kind(rows: list[Row]) -> str | None:
    kinds = {r["cost_kind"] for r in rows}
    if len(kinds) > 1:
        raise MixedBasisError(f"rows mix cost kinds {sorted(kinds)}; select one basis")
    return next(iter(kinds)) if kinds else None


def filter_rows(rows: Iterable[Row], start: date | None = None, end: date | None = None,
                currency: str | None = None) -> list[Row]:
    """Inclusive date range [start, end]; dates are ISO strings or date objects."""
    out = []
    for r in rows:
        dt = r["date"] if isinstance(r["date"], date) else date.fromisoformat(str(r["date"]))
        if start and dt < start:
            continue
        if end and dt > end:
            continue
        if currency and r["billing_currency"] != currency:
            continue
        out.append(r)
    return out


def total_cost(rows: Iterable[Row], currency: str) -> Decimal:
    """Signed sum of `cost` in one native currency and one cost basis."""
    rs = [r for r in rows if r["billing_currency"] == currency]
    _single_kind(rs)
    return sum((d(r["cost"]) for r in rs), ZERO)


def cost_by_charge_type(rows: Iterable[Row], currency: str) -> dict[str, Decimal]:
    rs = [r for r in rows if r["billing_currency"] == currency]
    _single_kind(rs)
    out: dict[str, Decimal] = {}
    for r in rs:
        out[r["charge_type"]] = out.get(r["charge_type"], ZERO) + d(r["cost"])
    return out


def reservation_utilization(rows: Iterable[Row], currency: str) -> Decimal | None:
    """used / (used + unused) in quantity (hours). Amortized basis only.

    used   = qty of Usage rows with pricing_model == 'Reservation'
    unused = qty of 'UnusedReservation' rows
    None when there is no reservation capacity in scope (never 0%).
    """
    rs = [r for r in rows if r["billing_currency"] == currency]
    if _single_kind(rs) not in (None, "amortized"):
        raise MixedBasisError("utilization needs the amortized export (it carries unused rows)")
    used = sum((d(r["quantity"]) for r in rs
                if r["charge_type"] == "Usage" and r["pricing_model"] == "Reservation"), ZERO)
    unused = sum((d(r["quantity"]) for r in rs if r["charge_type"] == "UnusedReservation"), ZERO)
    cap = used + unused
    return None if cap == 0 else used / cap


@dataclass(frozen=True)
class Coverage:
    covered: Decimal            # PayG-equivalent value of benefit-covered usage
    eligible: Decimal           # covered + uncovered on-demand eligible PayG-equivalent
    ratio: Decimal | None
    excluded_missing_price: int  # eligible rows skipped because payg_price was absent/unusable
    excluded_spot: int           # Spot rows are outside the denominator by definition
    contract_basis_rows: int = 0  # benefit rows valued at UnitPrice (contract) because PayGPrice was 0


def coverage(rows: Iterable[Row], currency: str, eligible_categories: set[str]) -> Coverage:
    """covered PayG-equiv / (covered + uncovered eligible on-demand PayG-equiv).

    Numerator and denominator use payg_price * quantity, NOT cost. Rows with a missing
    payg_price are excluded and counted, never valued at zero. Real exports carry PayGPrice = 0 on
    Reservation/SavingsPlan rows: such a 0 is "unknown", not free. If the row has payg_basis ==
    'contract' (set at import from UnitPrice) it is valued at that contract price and counted in
    contract_basis_rows so the UI can label the figure; otherwise it is excluded.
    """
    rs = [r for r in rows if r["billing_currency"] == currency and r["charge_type"] == "Usage"]
    _single_kind(rs)
    covered = uncovered = ZERO
    missing = spot = contract = 0
    for r in rs:
        if r["meter_category"] not in eligible_categories:
            continue
        pm = r["pricing_model"]
        if pm == "Spot":
            spot += 1
            continue
        if pm not in ("Reservation", "SavingsPlan", "On Demand"):
            continue
        unusable = not _has(r["payg_price"]) or (pm != "On Demand" and d(r["payg_price"]) <= 0)
        if unusable:
            missing += 1
            continue
        value = d(r["payg_price"]) * d(r["quantity"])
        if r.get("payg_basis") == "contract":
            contract += 1
        if pm == "On Demand":
            uncovered += value
        else:
            covered += value
    eligible = covered + uncovered
    return Coverage(covered, eligible, None if eligible == 0 else covered / eligible,
                    missing, spot, contract)


@dataclass(frozen=True)
class ObservedDiscount:
    payg_equivalent: Decimal   # PayG value of covered usage
    benefit_cost: Decimal      # amortized cost of that covered usage
    gross: Decimal             # payg_equivalent - benefit_cost
    unused_cost: Decimal       # cost of UnusedReservation / UnusedSavingsPlan rows
    net: Decimal               # gross - unused_cost
    excluded_missing_price: int


def observed_discount(rows: Iterable[Row], currency: str) -> ObservedDiscount:
    """Observed (historical, not estimated) benefit discount. Amortized basis only."""
    rs = [r for r in rows if r["billing_currency"] == currency]
    if _single_kind(rs) not in (None, "amortized"):
        raise MixedBasisError("observed discount needs the amortized export")
    payg = cost = unused = ZERO
    missing = 0
    for r in rs:
        if r["charge_type"] in ("UnusedReservation", "UnusedSavingsPlan"):
            unused += d(r["cost"])
        elif r["charge_type"] == "Usage" and r["pricing_model"] in ("Reservation", "SavingsPlan"):
            if not _has(r["payg_price"]) or d(r["payg_price"]) <= 0:
                missing += 1
                continue
            payg += d(r["payg_price"]) * d(r["quantity"])
            cost += d(r["cost"])
    gross = payg - cost
    return ObservedDiscount(payg, cost, gross, unused, gross - unused, missing)


@dataclass(frozen=True)
class PriceVolume:
    change: Decimal
    volume: Decimal
    price: Decimal
    new: Decimal        # cost of (meter, unit) groups absent in period 0
    removed: Decimal    # negative: cost of groups absent in period 1
    unattributed: Decimal  # groups with zero quantity in either period (no unit price)
    detail: dict[tuple[str, str], tuple[Decimal, Decimal]] = field(default_factory=dict)


def price_volume(rows0: Iterable[Row], rows1: Iterable[Row], currency: str) -> PriceVolume:
    """Decompose a cost change per (meter_id, unit): p = cost / quantity.

    volume = (q1 - q0) * p0 ;  price = (p1 - p0) * q1 = c1 - q1 * p0
    volume + price == c1 - c0 for every group present, with non-zero quantity, in both periods.
    """
    def group(rows: Iterable[Row]) -> dict[tuple[str, str], tuple[Decimal, Decimal]]:
        rs = [r for r in rows if r["billing_currency"] == currency and r["charge_type"] == "Usage"]
        _single_kind(rs)
        g: dict[tuple[str, str], list[Decimal]] = {}
        for r in rs:
            k = (r["meter_id"], r["unit"])
            a = g.setdefault(k, [ZERO, ZERO])
            a[0] += d(r["quantity"])
            a[1] += d(r["cost"])
        return {k: (v[0], v[1]) for k, v in g.items()}

    g0, g1 = group(rows0), group(rows1)
    vol = price = new = removed = unattr = ZERO
    detail: dict[tuple[str, str], tuple[Decimal, Decimal]] = {}
    for k in sorted(set(g0) | set(g1)):
        q0, c0 = g0.get(k, (ZERO, ZERO))
        q1, c1 = g1.get(k, (ZERO, ZERO))
        if k not in g0:
            new += c1
        elif k not in g1:
            removed -= c0
        elif q0 == 0 or q1 == 0:
            unattr += c1 - c0
        else:
            p0 = c0 / q0
            v, p = (q1 - q0) * p0, c1 - q1 * p0
            vol += v
            price += p
            detail[k] = (v, p)
    change = sum((c for _, c in g1.values()), ZERO) - sum((c for _, c in g0.values()), ZERO)
    return PriceVolume(change, vol, price, new, removed, unattr, detail)


def convert_amount(amount: Decimal, src: str, dst: str,
                   rates: Mapping[tuple[str, str], Decimal]) -> Decimal:
    """Presentation-only conversion: amount_dst = amount_src * rate(src->dst).

    `rates` holds the dated rate selected by the caller (with provenance). A missing rate raises.
    """
    if src == dst:
        return amount
    try:
        return amount * rates[(src, dst)]
    except KeyError as e:
        raise MissingRateError(f"no FX rate {src}->{dst}") from e
