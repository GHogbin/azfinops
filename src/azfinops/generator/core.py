"""Deterministic SYNTHETIC data generator. All prices, discounts and usage are invented.

Determinism: only random.Random(seed) and exact Decimal/integer arithmetic; no wall-clock, no
set/dict-order dependence, no transcendental floats. Same (seed, months, scale, start) => same bytes.
"""
from __future__ import annotations

import csv
import hashlib
import json
import random
from datetime import date, timedelta
from decimal import Decimal as D
from pathlib import Path
from typing import Any

from ..canonical import COLUMNS

SEASON = [D(x) for x in ("1.00", "0.95", "0.95", "1.00", "1.05", "1.05",
                         "0.90", "0.90", "1.05", "1.10", "1.25", "1.15")]
VM_SKUS = {  # sku -> (meter_id, PayG hourly, next smaller sku)  -- invented prices
    "D2s_v5": ("syn-vm-d2s", D("0.10"), None),
    "D4s_v5": ("syn-vm-d4s", D("0.20"), "D2s_v5"),
    "D8s_v5": ("syn-vm-d8s", D("0.40"), "D4s_v5"),
}
REGIONS = ["eastus", "westeurope", "uksouth"]
APPS = ["checkout", "catalog", "analytics", "search", "ml-platform"]
ENVS = ["prod", "staging", "dev"]
NEGOTIATED = D("0.95")   # synthetic contract factor vs PayG
RI_FACTOR = D("0.60")    # synthetic reservation rate vs on-demand unit price
SP_FACTOR = D("0.80")
SPOT_FACTOR = D("0.25")
GBP_PRICE_FACTOR = D("0.80")
ACCOUNTS = {"EA": ("EA-100001", "Synthetic Enterprise Enrollment", "USD"),
            "MCA": ("MCA-200001", "Synthetic Customer Agreement UK", "GBP")}
SUBS = [("syn-prod", "EA"), ("syn-nonprod", "EA"), ("syn-data", "EA"), ("syn-uk", "MCA")]


def _q(x: D, places: int = 6) -> D:
    return x.quantize(D(1).scaleb(-places))


def _add_months(d: date, n: int) -> date:
    y, m = divmod(d.month - 1 + n, 12)
    return date(d.year + y, m + 1, 1)


def _sub_id(i: int) -> str:
    return f"00000000-0000-4000-8000-{i:012d}"


class _Res:
    def __init__(self, rng: random.Random, i: int, kind: str, days: int) -> None:
        sub_name, agr = SUBS[rng.randrange(len(SUBS))]
        self.kind = kind
        self.sub_name, self.sub_id = sub_name, _sub_id(SUBS.index((sub_name, agr)) + 1)
        self.acct_id, self.acct_name, self.cur = ACCOUNTS[agr]
        self.app = APPS[rng.randrange(len(APPS))]
        self.env = ENVS[rng.randrange(len(ENVS))] if self.sub_name != "syn-prod" else "prod"
        self.region = "uksouth" if agr == "MCA" else REGIONS[rng.randrange(2)]
        self.name = f"{kind}-{self.app}-{self.env}-{i:04d}"
        self.rg = f"rg-{self.app}-{self.env}"
        self.rid = (f"/subscriptions/{self.sub_id}/resourceGroups/{self.rg}/providers/"
                    f"Synthetic.{kind}/{self.name}")
        self.tags = {} if rng.random() < 0.10 else {"app": self.app, "env": self.env}
        self.start = -1 if rng.random() < 0.70 else rng.randrange(int(days * 0.8))
        self.end = rng.randrange(int(days * 0.5), days) if rng.random() < 0.12 else days + 1
        self.sku = rng.choice(list(VM_SKUS)) if kind == "vm" else ""
        self.rightsize_day = (rng.randrange(60, days - 30) if kind == "vm" and rng.random() < 0.15
                              and self.sku != "D2s_v5" else None)
        self.hours = 12 if (self.env == "dev" and kind == "vm") else 24
        self.spot = kind == "vm" and self.env == "dev" and rng.random() < 0.3
        self.base = D(rng.randrange(50, 800))          # storage GB / SQL size / tokens base
        self.anomaly = (rng.randrange(30, days - 10), 5) if rng.random() < 0.05 else None
        self.noise = [D(rng.randrange(95, 106)) / 100 for _ in range(7)]


def _row(day: date, r: _Res, **kw: Any) -> dict[str, Any]:
    row = dict.fromkeys(COLUMNS, "")
    row.update(date=day.isoformat(), billing_account_id=r.acct_id, billing_account_name=r.acct_name,
               billing_currency=r.cur, subscription_id=r.sub_id, subscription_name=r.sub_name,
               resource_group=r.rg, resource_id=r.rid, resource_name=r.name, region=r.region,
               tags=json.dumps(r.tags, sort_keys=True), x_synthetic="true", publisher_type="Azure",
               publisher_name="Azure", frequency="UsageBased")
    row.update(kw)
    return row


def generate_dataset(out: Path, seed: int = 42, months: int = 13, scale: float = 1.0,
                     start: date = date(2025, 10, 1)) -> dict[str, Any]:
    out.mkdir(parents=True, exist_ok=True)
    rng = random.Random(seed)
    end = _add_months(start, months)
    days = (end - start).days
    def resource_count(base: int) -> int:
        return max(2, int(base * scale))
    res: list[_Res] = []
    for kind, count in (("vm", 24), ("sql", 8), ("storage", 12), ("ai", 5), ("web", 6)):
        res += [_Res(rng, i, kind, days) for i in range(resource_count(count))]

    ri_n, ri_start = max(2, int(4 * scale)), _add_months(start, 1)
    ri_hr = _q(VM_SKUS["D4s_v5"][1] * NEGOTIATED * RI_FACTOR)           # 0.114
    ri_daily = ri_hr * 24 * ri_n
    ri_purchase = ri_daily * 365
    sp_start, sp_hr = _add_months(start, 2), D("1.50") * max(1, int(scale))
    sp_daily = sp_hr * 24

    with (
        open(out / 'actual.csv', 'w', newline='') as af,
        open(out / 'amortized.csv', 'w', newline='') as mf,
    ):
        wa, wm = csv.DictWriter(af, COLUMNS), csv.DictWriter(mf, COLUMNS)
        wa.writeheader(); wm.writeheader()
        cnt = {"a": 0, "m": 0}
        sha = hashlib.sha256()

        def emit(a: dict[str, Any] | None, m: dict[str, Any] | None) -> None:
            if a is not None:
                wa.writerow(a); cnt["a"] += 1
            if m is not None:
                wm.writerow(m); cnt["m"] += 1

        ea_ri = _Res(random.Random(0), 0, "reservation", days)
        ea_ri.sub_name, ea_ri.sub_id = "syn-prod", _sub_id(1)
        ea_ri.acct_id, ea_ri.acct_name, ea_ri.cur = ACCOUNTS["EA"]
        ea_ri.rg, ea_ri.name = "rg-commitments", "ri-d4s-eastus"
        ea_ri.rid, ea_ri.tags, ea_ri.region = "/providers/Synthetic.Capacity/reservationOrders/syn-ri-1", {}, "eastus"
        ea_sp = _Res(random.Random(0), 0, "savingsplan", days)
        ea_sp.acct_id, ea_sp.acct_name, ea_sp.cur = ACCOUNTS["EA"]
        ea_sp.sub_name, ea_sp.sub_id, ea_sp.rg, ea_sp.name = "syn-prod", _sub_id(1), "rg-commitments", "sp-compute-1"
        ea_sp.rid, ea_sp.tags, ea_sp.region = "/providers/Synthetic.BillingBenefits/savingsPlanOrders/syn-sp-1", {}, "eastus"
        ea_ids = {ACCOUNTS["EA"][0]}

        for di in range(days):
            day = start + timedelta(days=di)
            season = SEASON[day.month - 1]
            slots: list[tuple[_Res, int, D, D, str]] = []   # VM slots: (res, hours, unit_price, payg, sku)
            for r in res:
                if not (r.start <= di < r.end):
                    continue
                noise = r.noise[di % 7]
                anomaly = D(8) if r.anomaly and r.anomaly[0] <= di < r.anomaly[0] + r.anomaly[1] else D(1)
                gbp = GBP_PRICE_FACTOR if r.cur == "GBP" else D(1)
                if r.kind == "vm":
                    sku = r.sku
                    if r.rightsize_day is not None and di >= r.rightsize_day:
                        sku = VM_SKUS[sku][2] or sku
                    mid, payg, _ = VM_SKUS[sku]
                    payg *= gbp
                    hours = int(r.hours * anomaly) if anomaly == 1 else 24
                    up = _q(payg * NEGOTIATED)
                    if r.spot:
                        c = _q(up * SPOT_FACTOR * hours)
                        kw = {"service_family": "Compute", "meter_category": "Virtual Machines", "meter_id": mid,
                                  "meter_name": f"{sku} Spot Hour", "sku": sku, "unit": "1 Hour", "quantity": str(hours),
                                  "unit_price": str(_q(up * SPOT_FACTOR)), "payg_price": str(payg), "cost": str(c),
                                  "charge_type": "Usage", "pricing_model": "Spot"}
                        row = _row(day, r, **kw); emit(row, dict(row))
                    else:
                        slots.append((r, hours, up, payg, sku))
                    continue
                if r.kind == "sql":
                    q, up, mid, cat, unit, fam = D(24), _q(D("0.35") * gbp * NEGOTIATED), "syn-sql-gp", "SQL Database", "1 Hour", "Databases"
                elif r.kind == "storage":
                    pf = D("1.08") if di >= 200 else D(1)  # synthetic price increase
                    q = _q(r.base * season * noise * anomaly / 30, 4)
                    up, mid, cat, unit, fam = _q(D("0.02") * gbp * pf, 6), "syn-stor-hot", "Storage", "1 GB/Month", "Storage"
                elif r.kind == "ai":
                    q = _q(r.base * 40 * season * noise * anomaly, 2)
                    up, mid, cat, unit, fam = _q(D("0.0015") * gbp, 6), f"syn-ai-model-{ord(r.name[-1]) % 3}", "Azure OpenAI", "1K Tokens", "AI + Machine Learning"
                else:
                    q, up, mid, cat, unit, fam = D(24), _q(D("0.10") * gbp * NEGOTIATED), "syn-web-p1", "App Service", "1 Hour", "Web"
                c = _q(q * up)
                row = _row(day, r, service_family=fam, meter_category=cat, meter_id=mid,
                           meter_name=mid, sku=mid, unit=unit, quantity=str(q), unit_price=str(up),
                           payg_price=str(up), cost=str(c), charge_type="Usage", pricing_model="On Demand")
                emit(row, dict(row))

            # ---- commitments on USD (EA) VM slots, deterministic order
            ea_slots = [s for s in slots if s[0].acct_id in ea_ids]
            other_slots = [s for s in slots if s[0].acct_id not in ea_ids]
            ri_cap = ri_n * 24 if day >= ri_start else 0
            ri_used = 0
            remaining: list[tuple[_Res, int, D, D, str]] = []
            for s in ea_slots:
                r, hours, up, payg, sku = s
                take = min(hours, ri_cap - ri_used) if (sku == "D4s_v5" and r.region == "eastus" and ri_cap) else 0
                if take:
                    ri_used += take
                    base = {"service_family": "Compute", "meter_category": "Virtual Machines", "meter_id": VM_SKUS[sku][0],
                                "meter_name": f"{sku} Hour", "sku": sku, "unit": "1 Hour", "quantity": str(take),
                                "payg_price": str(payg), "pricing_model": "Reservation", "reservation_id": "syn-ri-1",
                                "charge_type": "Usage"}
                    emit(_row(day, r, unit_price="0", cost="0", **base),
                         _row(day, r, unit_price=str(ri_hr), cost=str(_q(ri_hr * take)), **base))
                if hours - take:
                    remaining.append((r, hours - take, up, payg, sku))
            if ri_cap:
                unused = ri_cap - ri_used
                if unused:
                    emit(None, _row(day, ea_ri, service_family="Compute", meter_category="Virtual Machines",
                                    meter_id=VM_SKUS["D4s_v5"][0], meter_name="D4s_v5 Hour", sku="D4s_v5",
                                    unit="1 Hour", quantity=str(unused), unit_price=str(ri_hr),
                                    cost=str(_q(ri_hr * unused)), charge_type="UnusedReservation",
                                    pricing_model="Reservation", reservation_id="syn-ri-1"))
            if day == ri_start:
                emit(_row(day, ea_ri, service_family="Compute", meter_category="Reservations",
                          meter_id="syn-ri-purchase", meter_name=f"Reservation purchase D4s_v5 x{ri_n:d} 1y",
                          sku="D4s_v5", unit="1", quantity="1", unit_price=str(_q(ri_purchase)),
                          payg_price=str(_q(ri_purchase)), cost=str(_q(ri_purchase)), charge_type="Purchase",
                          pricing_model="Reservation", frequency="OneTime", reservation_id="syn-ri-1"), None)

            sp_budget = sp_daily if day >= sp_start else D(0)
            sp_spent = D(0)
            for r, hours, up, payg, sku in remaining:
                take = 0
                if sp_budget:
                    rate = _q(up * SP_FACTOR)
                    take = int(min(D(hours), (sp_budget - sp_spent) // rate)) if rate else 0
                if take:
                    cost_m = _q(rate * take); sp_spent += cost_m
                    base = {"service_family": "Compute", "meter_category": "Virtual Machines", "meter_id": VM_SKUS[sku][0],
                                "meter_name": f"{sku} Hour", "sku": sku, "unit": "1 Hour", "quantity": str(take),
                                "payg_price": str(payg), "pricing_model": "SavingsPlan", "benefit_id": "syn-sp-1",
                                "charge_type": "Usage"}
                    emit(_row(day, r, unit_price="0", cost="0", **base),
                         _row(day, r, unit_price=str(rate), cost=str(cost_m), **base))
                if hours - take:
                    h = hours - take
                    row = _row(day, r, service_family="Compute", meter_category="Virtual Machines",
                               meter_id=VM_SKUS[sku][0], meter_name=f"{sku} Hour", sku=sku, unit="1 Hour",
                               quantity=str(h), unit_price=str(up), payg_price=str(payg),
                               cost=str(_q(up * h)), charge_type="Usage", pricing_model="On Demand")
                    emit(row, dict(row))
            for r, hours, up, payg, sku in other_slots:  # non-EA (GBP) VMs: never commitment-covered here
                row = _row(day, r, service_family="Compute", meter_category="Virtual Machines",
                           meter_id=VM_SKUS[sku][0], meter_name=f"{sku} Hour", sku=sku, unit="1 Hour",
                           quantity=str(hours), unit_price=str(up), payg_price=str(payg),
                           cost=str(_q(up * hours)), charge_type="Usage", pricing_model="On Demand")
                emit(row, dict(row))
            if sp_budget:
                if sp_daily - sp_spent:
                    emit(None, _row(day, ea_sp, service_family="Compute", meter_category="Virtual Machines",
                                    meter_id="syn-sp-unused", meter_name="Savings plan unused", unit="1 Hour",
                                    quantity="1", unit_price="0", cost=str(_q(sp_daily - sp_spent)),
                                    charge_type="UnusedSavingsPlan", pricing_model="SavingsPlan",
                                    benefit_id="syn-sp-1"))
                if day.day == 1 or day == sp_start:
                    dim = (_add_months(date(day.year, day.month, 1), 1) - date(day.year, day.month, 1)).days
                    amt = _q(sp_daily * (dim - day.day + 1))
                    emit(_row(day, ea_sp, service_family="Compute", meter_category="Savings Plan",
                              meter_id="syn-sp-charge", meter_name="Savings plan monthly charge", unit="1",
                              quantity="1", unit_price=str(amt), payg_price=str(amt), cost=str(amt),
                              charge_type="Purchase", pricing_model="SavingsPlan", frequency="Recurring",
                              benefit_id="syn-sp-1"), None)

            if day.day == 1:  # monthly marketplace, credits; one refund and one adjustment later
                mp = res[0]
                for k, price in enumerate((D("450.00"), D("129.00"))):
                    row = _row(day, mp, resource_group="rg-marketplace", resource_name=f"mkt-{k}",
                               resource_id=f"/subscriptions/{mp.sub_id}/resourceGroups/rg-marketplace/providers/Synthetic.SaaS/mkt-{k}",
                               service_family="Marketplace", meter_category="Marketplace", meter_id=f"syn-mkt-{k}",
                               meter_name=f"Synthetic third-party SaaS {k}", unit="1", quantity="1",
                               unit_price=str(price), payg_price=str(price), cost=str(price), charge_type="Usage",
                               pricing_model="On Demand", publisher_type="Marketplace",
                               publisher_name=f"Synthetic Publisher {k}", frequency="Recurring",
                               billing_account_id=ACCOUNTS["EA"][0], billing_currency="USD",
                               billing_account_name=ACCOUNTS["EA"][1], subscription_id=_sub_id(1), subscription_name="syn-prod")
                    emit(row, dict(row))
                emit(_row(day, ea_ri, resource_group="", resource_id="", resource_name="", service_family="Credits",
                          meter_category="Credits", meter_id="syn-credit", meter_name="Synthetic promotional credit",
                          unit="1", quantity="1", unit_price="-300.00", cost="-300.00", charge_type="Credit",
                          frequency="OneTime"), None)
                idx = (day.year - start.year) * 12 + day.month - start.month
                if idx == 8:
                    emit(_row(day, ea_ri, resource_id="", service_family="Marketplace", meter_category="Marketplace",
                              meter_id="syn-mkt-0", meter_name="Synthetic third-party SaaS 0 refund", unit="1",
                              quantity="1", unit_price="-450.00", cost="-450.00", charge_type="Refund",
                              publisher_type="Marketplace", frequency="OneTime"), None)
                if idx == 9:
                    emit(_row(day, ea_ri, resource_id="", service_family="Adjustments", meter_category="Adjustments",
                              meter_id="syn-adj", meter_name="Synthetic billing adjustment", unit="1", quantity="1",
                              unit_price="120.00", cost="120.00", charge_type="Adjustment", frequency="OneTime"), None)

    with open(out / "exchange_rates.csv", "w", newline="") as f:
        w = csv.writer(f); w.writerow(["rate_date", "from_currency", "to_currency", "rate", "source", "is_synthetic"])
        for i in range(months):
            w.writerow([_add_months(start, i).isoformat(), "GBP", "USD", str(D("1.25") + D(i % 5) / 100),
                        "synthetic-generator", "true"])
    with open(out / "commitments.csv", "w", newline="") as f:
        w = csv.writer(f)
        w.writerow(["commitment_id", "kind", "name", "billing_currency", "term_months", "start_date",
                    "purchase_cost", "hourly_commitment", "region", "sku", "quantity", "is_synthetic"])
        w.writerow(["syn-ri-1", "Reservation", "ri-d4s-eastus", "USD", 12, ri_start.isoformat(),
                    str(_q(ri_purchase)), "", "eastus", "D4s_v5", ri_n, "true"])
        w.writerow(["syn-sp-1", "SavingsPlan", "sp-compute-1", "USD", 36, sp_start.isoformat(), "",
                    str(sp_hr), "", "", "", "true"])
    with open(out / "utilization.csv", "w", newline="") as f:
        w = csv.writer(f); w.writerow(["resource_id", "observed_date", "metric", "aggregation", "value", "unit", "source", "is_synthetic"])
        urng = random.Random(seed + 1)
        for r in res:
            if r.kind != "vm":
                continue
            idle = urng.random() < 0.2
            for wk in range(0, days, 7):
                if r.start <= wk < r.end:
                    v = urng.uniform(0.5, 3.0) if idle else urng.uniform(15.0, 70.0)
                    w.writerow([r.rid, (start + timedelta(days=wk)).isoformat(), "cpu_percent", "avg",
                                f"{v:.2f}", "percent", "synthetic-generator", "true"])
    for fn in ("actual.csv", "amortized.csv"):
        sha.update((out / fn).read_bytes())
    manifest = {"synthetic": True, "generator_version": 1, "seed": seed, "months": months, "scale": scale,
                "start": start.isoformat(), "actual_rows": cnt["a"], "amortized_rows": cnt["m"],
                "content_sha256": sha.hexdigest(),
                "note": "All prices, discounts, usage and commitments are invented and not Azure facts."}
    (out / "manifest.json").write_text(json.dumps(manifest, indent=2))
    return manifest
