# UNTESTED. Run from the repository root.
$ErrorActionPreference = "Stop"
if (-not (Get-Command uv -ErrorAction SilentlyContinue)) { throw "Install uv first: https://docs.astral.sh/uv/" }
uv sync
uv run pytest -q
