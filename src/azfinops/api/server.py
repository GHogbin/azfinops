"""`azfinops-serve`: run the API (and the built frontend, if present) on localhost only."""
from __future__ import annotations

import logging

import uvicorn

from ..config import Settings
from .app import LOCAL_HOSTS, create_app


def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    s = Settings.from_env()
    if s.host not in ("127.0.0.1", "localhost", "::1"):
        raise SystemExit(f"refusing to bind to {s.host!r}: this application is localhost-only "
                         "(AZFINOPS_HOST must be 127.0.0.1, localhost or ::1)")
    if not s.db_path.exists():
        raise SystemExit(f"database not found at {s.db_path}. Import data first (azfinops-import).")
    print(f"Serving on http://{s.host}:{s.port}  (API docs: /api/docs). Stop this server before importing.")
    uvicorn.run(create_app(s, allowed_hosts=LOCAL_HOSTS), host=s.host, port=s.port, log_level="info")


if __name__ == "__main__":
    main()
