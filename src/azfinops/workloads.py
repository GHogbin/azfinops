"""Grouping of meter categories into workload families (AI, data platform, databases).

This is a name heuristic, not an Azure taxonomy: a category belongs to the first family with a matching
substring (case-insensitive). The API returns every matched category so the grouping can be checked, and
anything unmatched stays outside the families rather than being guessed.
"""
from __future__ import annotations

from collections import defaultdict
from collections.abc import Iterable
from decimal import Decimal
from typing import Any

ZERO = Decimal(0)

FAMILIES: tuple[tuple[str, str, tuple[str, ...]], ...] = (
    ("ai", "AI services",
     ("openai", "cognitive", "foundry", "machine learning", "ai search", "azure ai", "ai services",
      "bot service", "speech", "document intelligence", "computer vision", "language service", "ai studio")),
    ("data", "Data platform",
     ("fabric", "synapse", "databricks", "data factory", "data lake", "stream analytics", "event hubs",
      "hdinsight", "power bi", "purview", "data explorer", "analysis services")),
    ("database", "Databases",
     ("sql", "cosmos", "postgres", "mysql", "mariadb", "redis", "database", "managed instance")),
)


def classify(category: str | None) -> str | None:
    if not category:
        return None
    c = category.lower()
    for fid, _name, patterns in FAMILIES:
        if any(p in c for p in patterns):
            return fid
    return None


def build(rows: Iterable[tuple[str | None, Any, Decimal]], top_categories: int = 15) -> dict[str, Any]:
    """rows: (meter_category, month, cost). Pure function: no database access."""
    cells: dict[tuple[str | None, Any], Decimal] = defaultdict(lambda: ZERO)
    for cat, month, cost in rows:
        cells[(cat, month)] += cost
    months = sorted({m for _, m in cells})
    total = sum(cells.values(), ZERO)
    fam: dict[str, dict[str | None, dict[Any, Decimal]]] = {f[0]: defaultdict(dict) for f in FAMILIES}
    other = ZERO
    for (cat, month), cost in cells.items():
        fid = classify(cat)
        if fid is None:
            other += cost
        else:
            fam[fid][cat][month] = cost
    groups = []
    for fid, name, patterns in FAMILIES:
        cats: list[dict[str, Any]] = []
        for cat, by in fam[fid].items():
            cats.append({"category": cat, "total": sum(by.values(), ZERO),
                         "months": [by.get(m, ZERO) for m in months]})
        cats.sort(key=lambda c: (-abs(c["total"]), str(c["category"])))
        gtotal = sum((c["total"] for c in cats), ZERO)
        shown = cats[:top_categories]
        groups.append({
            "id": fid, "name": name, "matches": list(patterns), "total": gtotal,
            "months": [sum((by.get(m, ZERO) for by in fam[fid].values()), ZERO) for m in months],
            "category_count": len(cats), "categories": shown,
            "other": gtotal - sum((c["total"] for c in shown), ZERO),
        })
    return {"months": months, "total": total, "outside_families": other, "groups": groups,
            "definition": ("Services are grouped by matching words in the meter category name, for example "
                           "'sql' or 'fabric'. It is a name match, not an Azure classification, so check the "
                           "listed services. Anything that does not match stays outside the groups.")}
