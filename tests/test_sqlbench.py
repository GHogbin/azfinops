"""SQL workbench validation and bounded execution (needs duckdb only)."""
import pytest

duckdb = pytest.importorskip("duckdb")

from azfinops import sqlbench
from azfinops.sqlbench import SqlRejected


@pytest.fixture
def con():
    c = duckdb.connect(":memory:")
    c.execute("CREATE TABLE t AS SELECT i, i * 1.5::DECIMAL(10,2) AS d, 'x' AS s FROM range(5) r(i)")
    return c


@pytest.mark.parametrize("sql", [
    "SELECT 1", "select * from t", "WITH a AS (SELECT 1 AS x) SELECT * FROM a", "SELECT 1;", "  SELECT 1  ",
    "FROM t SELECT i",
])
def test_select_forms_are_accepted(con, sql):
    assert sqlbench.validate(con, sql).strip().upper().startswith(("SELECT", "WITH", "FROM"))


@pytest.mark.parametrize("sql", [
    "DROP TABLE t", "INSERT INTO t VALUES (9, 1, 'y')", "UPDATE t SET i = 1", "DELETE FROM t",
    "CREATE TABLE u (i INT)", "COPY t TO 'x.csv'", "ATTACH 'x.db'", "PRAGMA database_list", "CALL pragma_version()",
    "SET threads = 1", "INSTALL httpfs", "LOAD httpfs", "EXPORT DATABASE 'x'", "VACUUM", "ALTER TABLE t ADD COLUMN z INT",
])
def test_anything_but_select_is_rejected(con, sql):
    with pytest.raises(SqlRejected):
        sqlbench.validate(con, sql)


def test_multiple_statements_empty_and_oversized_are_rejected(con):
    with pytest.raises(SqlRejected, match="one statement"):
        sqlbench.validate(con, "SELECT 1; SELECT 2")
    with pytest.raises(SqlRejected, match="one statement"):
        sqlbench.validate(con, "SELECT 1; DROP TABLE t")
    for blank in ("", "   ", ";", "-- nothing"):
        with pytest.raises(SqlRejected):
            sqlbench.validate(con, blank)
    with pytest.raises(SqlRejected, match="longer"):
        sqlbench.validate(con, "SELECT '" + "x" * sqlbench.MAX_SQL_CHARS + "'")


def test_parse_error_is_a_clean_rejection(con):
    with pytest.raises(SqlRejected, match="parse"):
        sqlbench.validate(con, "SELEC 1")


def test_run_returns_columns_types_and_json_safe_values(con):
    r = sqlbench.run(con, "SELECT i, d, s, DATE '2026-09-01' AS day, NULL AS n, 'é' AS u FROM t ORDER BY i LIMIT 2")
    assert r.columns == ["i", "d", "s", "day", "n", "u"] and len(r.types) == 6
    assert r.rows[1] == [1, "1.50", "x", "2026-09-01", None, "é"]
    assert r.truncated is False


def test_row_cap_marks_truncation(con):
    r = sqlbench.run(con, "SELECT * FROM range(10)", limit=4)
    assert len(r.rows) == 4 and r.truncated is True and r.limit == 4
    r = sqlbench.run(con, "SELECT * FROM range(4)", limit=4)
    assert len(r.rows) == 4 and r.truncated is False
    r = sqlbench.run(con, "SELECT * FROM range(5000)", limit=10**6)
    assert len(r.rows) == sqlbench.MAX_ROWS and r.truncated is True


def test_long_text_and_odd_numbers_are_made_safe(con):
    r = sqlbench.run(con, "SELECT repeat('a', 2000) AS s, 'nan'::DOUBLE AS f, 'inf'::DOUBLE AS g, 1e300::DOUBLE AS h")
    s, f, g, h = r.rows[0]
    assert len(s) == sqlbench.MAX_CELL_CHARS + 1 and s.endswith("…")
    assert f is None and g is None and h == 1e300


def test_too_many_columns_and_runtime_errors_are_rejections(con):
    cols = ", ".join(f"{i} AS c{i}" for i in range(sqlbench.MAX_COLUMNS + 1))
    with pytest.raises(SqlRejected, match="columns"):
        sqlbench.run(con, f"SELECT {cols}")
    with pytest.raises(SqlRejected):
        sqlbench.run(con, "SELECT * FROM no_such_table")
    with pytest.raises(SqlRejected):
        sqlbench.run(con, "SELECT CAST('abc' AS INTEGER)")


def test_locked_down_connection_cannot_read_files(tmp_path):
    f = tmp_path / "x.csv"
    f.write_text("a\n1\n", encoding="utf-8")
    c = duckdb.connect(":memory:")
    c.execute("SET enable_external_access=false")
    c.execute("SET lock_configuration=true")
    with pytest.raises(SqlRejected):
        sqlbench.run(c, f"SELECT * FROM read_csv('{f.as_posix()}')")
    with pytest.raises(duckdb.Error):
        c.execute("SET enable_external_access=true")
