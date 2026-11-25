"""Rebuilds tests/fixtures/ea_amortized/*.csv. All values are INVENTED; only the layout (55 EA
columns, CRLF, MM/DD/YYYY dates, 'OnDemand', brace-less Tags, PayGPrice 0 on reservation rows)
mirrors what was observed in a real EA amortized export header/sample."""
import csv
from pathlib import Path

H = ["BillingAccountId", "BillingAccountName", "BillingPeriodStartDate", "BillingPeriodEndDate", "BillingProfileId", "BillingProfileName", "AccountOwnerId", "AccountName", "SubscriptionId", "SubscriptionName", "Date", "Product", "PartNumber", "MeterId", "ServiceFamily", "MeterCategory", "MeterSubCategory", "MeterRegion", "MeterName", "Quantity", "EffectivePrice", "Cost", "UnitPrice", "BillingCurrency", "ResourceLocation", "AvailabilityZone", "ConsumedService", "ResourceId", "ResourceName", "ServiceInfo1", "ServiceInfo2", "AdditionalInfo", "Tags", "InvoiceSectionId", "InvoiceSection", "CostCenter", "UnitOfMeasure", "ResourceGroup", "ReservationId", "ReservationName", "ProductOrderId", "ProductOrderName", "OfferId", "IsAzureCreditEligible", "Term", "PublisherName", "PlanName", "ChargeType", "Frequency", "PublisherType", "PayGPrice", "PricingModel", "CostAllocationRuleName", "benefitId", "benefitName"]
assert len(H) == 55


def row(**kw):
    d = dict.fromkeys(H, "")
    d.update(BillingAccountId="ACCT-1", BillingAccountName="Invented account", BillingPeriodStartDate="09/01/2026",
             BillingPeriodEndDate="09/30/2026", SubscriptionId="AAAAAAAA-0000-4000-8000-000000000001",
             SubscriptionName="invented-sub", Date="09/02/2026", ServiceFamily="Compute",
             MeterCategory="Virtual Machines", BillingCurrency="GBP", ResourceLocation="uksouth",
             ConsumedService="Microsoft.Compute", ResourceId="/subscriptions/x/resourceGroups/rg1/providers/p/vm1",
             ResourceGroup="rg1", ChargeType="Usage", Frequency="UsageBased", PublisherType="Azure",
             PricingModel="OnDemand", UnitOfMeasure="1 Hour", OfferId="MS-AZR-0017P", IsAzureCreditEligible="True")
    d.update(kw)
    return d


A = row(MeterId="m-a", MeterName="Meter A", Quantity="10", EffectivePrice="0.5", UnitPrice="0.5", Cost="5",
        PayGPrice="0.5")
ROWS = [
    A, dict(A),  # identical rows: legitimate duplicates, both must load
    row(MeterId="m-free", MeterName="Free egress", MeterCategory="Bandwidth", UnitOfMeasure="1 GB",
        Quantity="0.001", EffectivePrice="0", UnitPrice="0", Cost="0", PayGPrice="0.067"),
    row(MeterId="m-ri", MeterName="Meter RI", Quantity="4", EffectivePrice="0.4", UnitPrice="0.8", Cost="1.6",
        PayGPrice="0", PricingModel="Reservation", ReservationId="R1", Term="12", benefitId="B-R1"),
    row(MeterId="m-sp", MeterName="Meter SP", Quantity="5", EffectivePrice="0.07", UnitPrice="0.1", Cost="0.35",
        PayGPrice="0.15", PricingModel="SavingsPlan", Term="36", benefitId="B-SP1"),
    row(MeterId="m-ri", MeterName="Meter RI", ChargeType="UnusedReservation", Quantity="2", EffectivePrice="0.4",
        UnitPrice="0", Cost="0.8", PayGPrice="0", PricingModel="Reservation", ReservationId="R1", Term="12",
        benefitId="B-R1", ConsumedService="Microsoft.Capacity"),
    row(MeterId="m-ri2", MeterName="Meter RI2", ChargeType="UnusedReservation", Quantity="0",
        EffectivePrice="0.04", UnitPrice="0", Cost="0", PayGPrice="0", PricingModel="Reservation",
        ReservationId="R2", Term="12", benefitId="B-R2", ConsumedService="Microsoft.Capacity"),
    row(MeterId="m-mkt", MeterName="Third-party SaaS tokens", MeterCategory="SaaS", UnitOfMeasure="",
        Quantity="100", EffectivePrice="0.01", UnitPrice="0.01", Cost="1", PayGPrice="0.011",
        PublisherType="Marketplace", PublisherName="Invented Publisher", ConsumedService="Microsoft.SaaS"),
    row(MeterId="m-st", MeterName="Hot storage", MeterCategory="Storage", UnitOfMeasure="1 GB/Month",
        Quantity="1", EffectivePrice="2.5", UnitPrice="2.5", Cost="2.5", PayGPrice="2.5",
        Tags='"app": "x","env": "dev"'),
    row(MeterId="m-bad1", Date="13/45/2026", Quantity="1", Cost="1"),      # invalid date -> rejected
    row(MeterId="m-bad2", Quantity="1", Cost="abc"),                       # invalid cost -> rejected
]
out = Path(__file__).parent / "ea_amortized"
out.mkdir(exist_ok=True)
with open(out / "ea_amortized_sep.csv", "w", newline="", encoding="utf-8") as f:
    w = csv.DictWriter(f, H, lineterminator="\r\n")
    w.writeheader()
    w.writerows(ROWS)
with open(out / "second_partition.csv", "w", newline="", encoding="utf-8") as f:  # same period, other rows
    w = csv.DictWriter(f, H, lineterminator="\r\n")
    w.writeheader()
    w.writerow(row(MeterId="m-p2", MeterName="Partition two", Quantity="2", EffectivePrice="1", UnitPrice="1",
                   Cost="2", PayGPrice="1", Date="09/20/2026"))
with open(out / "minimal.csv", "w", newline="", encoding="utf-8") as f:  # required columns only
    f.write("BillingAccountId,Date,Cost,BillingCurrency,ChargeType\r\nACCT-9,2026-10-03,7.5,USD,Usage\r\n")
with open(out / "overlap_resubmission.csv", "w", newline="", encoding="utf-8") as f:  # same month, different bytes
    w = csv.DictWriter(f, H, lineterminator="\r\n")
    w.writeheader()
    w.writerow(row(MeterId="m-new", MeterName="Corrected export", Quantity="1", EffectivePrice="9", UnitPrice="9",
                   Cost="9", PayGPrice="9", Date="09/02/2026"))
print("fixtures written")
