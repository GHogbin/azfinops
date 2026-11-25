# azfinops

A local-first Azure FinOps dashboard for Enterprise Agreement cost exports, built with
Python, FastAPI, DuckDB, React and TypeScript. It runs on Windows and binds to localhost.

The repository contains application source, reusable reporting tools and synthetic test fixtures.
It does **not** contain billing exports, customer workbooks, databases, generated reports,
credentials, migration backups, local account notes or transfer archives.
Enrollment names and identifiers in test fixtures are invented. Reporting labels, native currencies,
default enrollment selection and chart colours are derived from supplied data, not a particular customer.

## Main dashboard

Workspaces cover portfolio overview, cost exploration and analysis, applications, workload families,
commitments, optimization, pricing, data management and an in-app knowledge guide.

- Native money stays signed and exact. Actual and amortized costs are never mixed.
- Combined views convert only with saved monthly rates and identify estimated or missing rates.
- Imports validate typed data and preserve original file lineage.
- Successful cost imports retain one shared latest-12-calendar-month window across the portfolio.
- Failed post-commit aggregate rebuilds block stale analysis until recovery.
- Fast integrity checks run automatically; full reconciliation is explicitly requested.
- Savings-plan figures are ceilings, not hourly purchase recommendations.

### Setup

Prerequisites: Windows, Python 3.12+, `uv`, and Node.js 22+.

```powershell
git clone https://github.com/GHogbin/azfinops.git
Set-Location -LiteralPath '.\azfinops'
uv sync --locked
Set-Location -LiteralPath '.\web'
npm ci
npm test
npm run build
Set-Location -LiteralPath '..'
```

Use your organisation's approved Python package index where direct package downloads are restricted.
Do not disable TLS certificate verification.

Data defaults to `%LOCALAPPDATA%\azfinops`; `AZFINOPS_DATA_DIR` selects another folder. This application
does not automatically load `.env`; configure the environment explicitly.

With the server stopped, import all partitions of one export as one batch:

```powershell
uv run --no-sync azfinops-import 'C:\data\usage.csv' --kind amortized
uv run --no-sync azfinops-serve
```

Open <http://127.0.0.1:8765>. The import command applies migrations, validates the export and rebuilds
the affected aggregates. Dashboard imports use the allowed import folders and pause readers while writing.
There is no bundled production database.

For an existing database, stop its server before applying pending migrations:

```powershell
uv run --no-sync azfinops-migrate 'C:\data\azfinops.duckdb'
```

Migrations are forward-only, checksummed and backed up before application. Never edit an applied migration.

### Development checks

```powershell
uv run --no-sync pytest -q --basetemp='.pytest_tmp' -p no:cacheprovider
uv run --no-sync ruff check --config 'pyproject.toml' --no-cache src tests
uv run --no-sync mypy src\azfinops
Set-Location -LiteralPath '.\web'
npm test
npm run build
```

The Windows pytest flags avoid locked system temporary folders.

## Standalone reporting

Reusable report adapters and a separate local reporting service live in `scripts\` and `reporting\mbr\`.
They accept supplied workbook or cost/Advisor CSV data and keep report outputs outside version control.
The reporting service is separate from the main DuckDB application and normally uses port 8766.

```powershell
python 'scripts\serve_mbr_dashboard.py' --help
python 'scripts\ingest_mbr_usage.py' --help
```

Workbook loading requires Windows Excel and pywin32 in the Python installation running the reporting
service; see `reporting\mbr\requirements.txt`. CSV loading does not require Excel. Use each tool's help
for input formats, explicit rates, preview/apply safeguards and isolated report paths.

## Documentation

- [Architecture](../docs/architecture.md)
- [Financial methodology](../docs/methodology.md)
- [Data model and retention](../docs/data-model.md)
- [Synthetic fixture expectations](../docs/fixtures.md)
- [Design system](../docs/design-system.md)

Local deployment notes and account-specific diagnostics are intentionally excluded. Generated reports,
exports and financial workbooks must stay in approved local storage and must never be committed here.

## Verification

Sanitized release checks on Windows (2026-11-25): 453 Python tests, 48 frontend tests and 101 standalone
reporting tests passed. Ruff, the main backend's strict mypy check, TypeScript and the Vite production build
passed. Public source contains invented test identities rather than customer references. Production
financial data was not uploaded, and publication did not change or migrate a database.
