"""Runtime settings from environment variables (no secrets). Data lives outside the repository."""
from __future__ import annotations

import os
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from duckdb import DuckDBPyConnection

_SIZE = re.compile(r"^\d+(\.\d+)?\s?(KB|MB|GB|TB)$", re.IGNORECASE)
_TAG_KEY = re.compile(r"^[A-Za-z0-9_.:\- ]{1,64}$")  # used inside a JSON path: no quotes allowed


def _tag_keys(raw: str | None, default: tuple[str, ...]) -> tuple[str, ...]:
    keys = tuple(k.strip() for k in raw.split(",") if k.strip()) if raw else default
    for k in keys:
        if not _TAG_KEY.match(k):
            raise ValueError(f"invalid tag key {k!r}: letters, digits and _ . : - space only")
    return keys


def _default_data_dir() -> Path:
    base = os.environ.get("LOCALAPPDATA") or os.path.expanduser("~/.local/share")
    return Path(base) / "azfinops"


@dataclass(frozen=True)
class Settings:
    data_dir: Path = field(default_factory=_default_data_dir)
    host: str = "127.0.0.1"
    port: int = 8765
    memory_limit: str = "2GB"
    threads: int = 2
    query_timeout_s: int = 30
    max_upload_mb: int = 512
    max_concurrent_queries: int = 4
    import_dirs: tuple[Path, ...] = ()
    # Tag keys (exact, case-sensitive, first non-empty wins) used for attribution.
    # Application ids are lower-cased at aggregation. ApplicationID is a fallback seen on rows that
    # lack mnd-applicationid; the primary key always wins when present.
    app_tag_keys: tuple[str, ...] = ("mnd-applicationid", "ApplicationID")
    env_tag_keys: tuple[str, ...] = ("mnd-envtype",)
    # False (default): the export's PayGPrice column is not imported and UnitPrice (the contract price) is the baseline
    # for coverage, observed discount and the pricing comparison. True restores PayGPrice.
    use_payg_price: bool = False

    @property
    def db_path(self) -> Path:
        return self.data_dir / "azfinops.duckdb"

    @property
    def allowed_import_dirs(self) -> tuple[Path, ...]:
        """Folders the dashboard may import from: AZFINOPS_IMPORT_DIRS, or <data dir>\\imports plus the project's
        samples folder when it exists. The command-line importer is not limited to these."""
        if self.import_dirs:
            return self.import_dirs
        samples = Path(__file__).resolve().parents[2] / "samples"
        return (self.data_dir / "imports", *((samples,) if samples.is_dir() else ()))

    @staticmethod
    def from_env(env: dict[str, str] | None = None) -> Settings:
        e = os.environ if env is None else env
        s = Settings(
            data_dir=Path(e["AZFINOPS_DATA_DIR"]) if e.get("AZFINOPS_DATA_DIR") else _default_data_dir(),
            host=e.get("AZFINOPS_HOST", "127.0.0.1"),
            port=int(e.get("AZFINOPS_PORT", "8765")),
            memory_limit=e.get("AZFINOPS_MEMORY_LIMIT", "2GB"),
            threads=int(e.get("AZFINOPS_THREADS", "2")),
            query_timeout_s=int(e.get("AZFINOPS_QUERY_TIMEOUT_S", "30")),
            max_upload_mb=int(e.get("AZFINOPS_MAX_UPLOAD_MB", "512")),
            max_concurrent_queries=int(e.get("AZFINOPS_MAX_CONCURRENT_QUERIES", "4")),
            import_dirs=tuple(Path(p) for p in e.get("AZFINOPS_IMPORT_DIRS", "").split(os.pathsep) if p),
            app_tag_keys=_tag_keys(e.get("AZFINOPS_APP_TAG_KEYS"), ("mnd-applicationid", "ApplicationID")),
            env_tag_keys=_tag_keys(e.get("AZFINOPS_ENV_TAG_KEYS"), ("mnd-envtype",)),
            use_payg_price=e.get("AZFINOPS_USE_PAYG_PRICE", "0").strip().lower() in ("1", "true", "yes"),
        )
        if not _SIZE.match(s.memory_limit):
            raise ValueError(f"AZFINOPS_MEMORY_LIMIT must look like '2GB', got {s.memory_limit!r}")
        if not 1 <= s.threads <= 64:
            raise ValueError("AZFINOPS_THREADS must be 1..64")
        if not 1 <= s.max_concurrent_queries <= 32:
            raise ValueError("AZFINOPS_MAX_CONCURRENT_QUERIES must be 1..32")
        if not 1 <= s.query_timeout_s <= 600:
            raise ValueError("AZFINOPS_QUERY_TIMEOUT_S must be 1..600")
        return s

    def apply(self, con: DuckDBPyConnection) -> None:
        con.execute(f"SET memory_limit='{self.memory_limit}'")
        con.execute(f"SET threads={int(self.threads)}")
