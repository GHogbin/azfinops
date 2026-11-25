"""Workload grouping is pure (no database). Expected values are worked out by hand in the comments."""
from datetime import date
from decimal import Decimal as D

import pytest

from azfinops import workloads as w

AUG, SEP = date(2026, 8, 1), date(2026, 9, 1)


@pytest.mark.parametrize("category,family", [
    ("SQL Database", "database"), ("SQL Managed Instance", "database"), ("Redis Cache", "database"),
    ("Azure Database for PostgreSQL", "database"), ("Azure Cosmos DB", "database"),
    ("Microsoft Fabric", "data"), ("Azure Synapse Analytics", "data"), ("Azure Databricks", "data"),
    ("Azure OpenAI", "ai"), ("Cognitive Services", "ai"), ("Azure AI Search", "ai"), ("Azure Machine Learning", "ai"),
    ("Log Analytics", None), ("Storage", None), ("Virtual Machines", None), ("", None), (None, None),
])
def test_classify(category, family):
    assert w.classify(category) == family


ROWS = [("SQL Database", AUG, D(10)), ("SQL Database", SEP, D(12)), ("Microsoft Fabric", AUG, D(5)),
        ("Microsoft Fabric", SEP, D(30)), ("Azure OpenAI", SEP, D(7)), ("Storage", AUG, D(100)),
        ("Log Analytics", SEP, D(4)), ("SQL Managed Instance", SEP, D(-2))]


def test_groups_months_and_totals_reconcile_by_hand():
    r = w.build(ROWS)
    # total = 10+12+5+30+7+100+4-2 = 166 ; outside = Storage 100 + Log Analytics 4 = 104
    assert r["months"] == [AUG, SEP] and r["total"] == D(166) and r["outside_families"] == D(104)
    g = {x["id"]: x for x in r["groups"]}
    assert (g["ai"]["total"], g["ai"]["months"]) == (D(7), [D(0), D(7)])
    assert (g["data"]["total"], g["data"]["months"]) == (D(35), [D(5), D(30)])
    assert (g["database"]["total"], g["database"]["months"]) == (D(20), [D(10), D(10)])   # Sep 12 - 2
    assert [c["category"] for c in g["database"]["categories"]] == ["SQL Database", "SQL Managed Instance"]
    assert sum(x["total"] for x in r["groups"]) + r["outside_families"] == r["total"]


def test_truncation_keeps_the_remainder():
    g = {x["id"]: x for x in w.build(ROWS, top_categories=1)["groups"]}["database"]
    assert [c["category"] for c in g["categories"]] == ["SQL Database"]
    assert g["category_count"] == 2 and g["other"] == D(-2)
    assert g["categories"][0]["total"] + g["other"] == g["total"]


def test_empty_input():
    r = w.build([])
    assert r["months"] == [] and r["total"] == 0 and all(x["total"] == 0 for x in r["groups"])
