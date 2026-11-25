"""Canonical CSV column contract shared by the generator, fixtures and importer.

This is OUR import format. Azure export mappers (EA/MCA -> canonical) are built in
phase 3 and are documented in docs/data-model.md.
"""
from __future__ import annotations

COLUMNS: list[str] = [
    "date", "billing_account_id", "billing_account_name", "billing_currency",
    "subscription_id", "subscription_name", "resource_group", "resource_id", "resource_name",
    "region", "service_family", "meter_category", "meter_id", "meter_name", "sku", "unit",
    "quantity", "unit_price", "payg_price", "cost", "charge_type", "pricing_model",
    "publisher_type", "publisher_name", "frequency", "reservation_id", "benefit_id", "tags",
    "x_synthetic",
]
REQUIRED: set[str] = {"date", "billing_account_id", "billing_currency", "subscription_id",
                      "cost", "charge_type"}
CHARGE_TYPES = {"Usage", "Purchase", "Refund", "UnusedReservation", "UnusedSavingsPlan",
                "Credit", "Adjustment"}
PRICING_MODELS = {"On Demand", "Reservation", "SavingsPlan", "Spot", ""}
COST_KINDS = {"actual", "amortized"}
