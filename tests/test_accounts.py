import pytest

pytest.importorskip("pyarrow")

from azfinops import accounts


def test_names_come_from_the_export_and_merge_with_manual_ones(tmp_path):
    csv = tmp_path / "Detail.csv"
    csv.write_text("BillingAccountId,BillingAccountName,Cost\n"
                   "100001,Contoso US,1\n100001,Contoso US,2\n100001,,3\n200001,Contoso UK,4\n", encoding="utf-8")
    assert accounts.harvest(csv) == {"100001": "Contoso US", "200001": "Contoso UK"}
    d = tmp_path / "data"
    assert accounts.harvest_and_store(d, [csv]) == {"100001": "Contoso US", "200001": "Contoso UK"}
    assert accounts.store(d, {"100001": "  US enterprise  ", "x": " "})["100001"] == "US enterprise"
    assert accounts.load(d) == {"100001": "US enterprise", "200001": "Contoso UK"}


def test_a_file_without_the_columns_gives_no_names(tmp_path):
    csv = tmp_path / "Other.csv"
    csv.write_text("A,B\n1,2\n", encoding="utf-8")
    assert accounts.harvest(csv) == {}
    assert accounts.load(tmp_path / "nothing-here") == {}
