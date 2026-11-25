from datetime import date

import pytest

from azfinops.config import Settings
from azfinops.queries import DIMENSIONS, Filters


def test_filters_require_one_basis_and_one_currency():
    with pytest.raises(ValueError):
        Filters("both", "GBP")
    with pytest.raises(ValueError):
        Filters("actual", "gbp")
    with pytest.raises(ValueError):
        Filters("actual", "GBP", date(2026, 2, 1), date(2026, 1, 1))


def test_where_is_fully_parameterised():
    f = Filters("amortized", "GBP", date(2026, 9, 1), date(2026, 9, 30),
                subscription_ids=("a'; DROP TABLE x;--", "b"), applications=("x",))
    sql, params = f.where()
    assert sql.count("?") == len(params)
    assert "DROP" not in sql and "a'" not in sql          # values only ever travel as parameters
    assert params[0:2] == ["amortized", "GBP"]


def test_dimension_whitelist_cannot_be_extended_by_callers():
    assert "cost; DROP TABLE x" not in DIMENSIONS and "subscription_id" in DIMENSIONS.values()


def test_tag_key_settings_are_validated():
    s = Settings.from_env({"AZFINOPS_APP_TAG_KEYS": "mnd-applicationid, ProjectCode"})
    assert s.app_tag_keys == ("mnd-applicationid", "ProjectCode")
    # the default is ordered: the primary key wins, ApplicationID is only a fallback
    assert Settings().app_tag_keys == ("mnd-applicationid", "ApplicationID")
    assert Settings.from_env({}).app_tag_keys == ("mnd-applicationid", "ApplicationID")
    with pytest.raises(ValueError):
        Settings.from_env({"AZFINOPS_APP_TAG_KEYS": 'bad"key'})
