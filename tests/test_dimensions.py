"""Needs duckdb + pyarrow."""
import pytest

duckdb = pytest.importorskip("duckdb")
pytest.importorskip("pyarrow")

from azfinops.dimensions import harvest_and_store, harvest_subscriptions
from azfinops.migrations.runner import migrate

CSV = (
    "SubscriptionId,SubscriptionName,Cost\n"
    "AAA-1,Prod Core,1\n"
    "aaa-1,Prod Core,2\n"
    "aaa-1,Prod Core (old),3\n"
    "bbb-2,Dev,4\n"
    "ccc-3,,5\n"          # blank name: ignored
    ",Orphan,6\n"         # blank id: ignored
)


def test_harvest_normalises_ids_ignores_blanks_and_counts_variants(tmp_path):
    p = tmp_path / "x.csv"
    p.write_text(CSV, encoding="utf-8")
    got = harvest_subscriptions(p)
    assert set(got) == {"aaa-1", "bbb-2"}
    assert got["aaa-1"]["Prod Core"] == 2 and got["aaa-1"]["Prod Core (old)"] == 1


def test_store_keeps_most_frequent_name_and_is_idempotent(tmp_path):
    p, db = tmp_path / "x.csv", tmp_path / "t.duckdb"
    p.write_text(CSV, encoding="utf-8")
    migrate(db)
    assert harvest_and_store(db, [p]) == (2, 1)
    assert harvest_and_store(db, [p]) == (2, 1)      # re-running replaces, never duplicates
    con = duckdb.connect(str(db), read_only=True)
    rows = con.execute("SELECT subscription_id, subscription_name, name_variants FROM dim_subscription "
                       "ORDER BY 1").fetchall()
    con.close()
    assert rows == [("aaa-1", "Prod Core", 2), ("bbb-2", "Dev", 1)]


def test_file_without_name_column_yields_nothing(tmp_path):
    p = tmp_path / "y.csv"
    p.write_text("SubscriptionId,Cost\nx,1\n", encoding="utf-8")
    assert harvest_subscriptions(p) == {}
