"""Local report uploads with validation, explicit replacement and two-month persistence.

Run: python scripts\\serve_mbr_dashboard.py
Open: http://127.0.0.1:8766
"""
from __future__ import annotations

import argparse
import csv
import hashlib
import json
import logging
import secrets
import tempfile
import threading
import time
from collections.abc import Mapping
from dataclasses import dataclass
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any
from urllib.parse import parse_qs, unquote, urlsplit

import build_mbr_dashboard as mbr
import mbr_uploads as uploads

LOG = logging.getLogger("mbr-report")


class ReportConflict(ValueError):
    pass


@dataclass
class PendingUpload:
    id: str
    revision: str
    created: float
    result: dict[str, Any]
    preview: dict[str, Any]


class ReportStore:
    def __init__(self, path: Path, fx_file: Path | None = None) -> None:
        self.path = path
        self.fx_file = fx_file
        self.lock = threading.Lock()
        self.pending: PendingUpload | None = None
        current = mbr.read_dashboard(path)
        snapshot, _preview = uploads.update_report(current, fx_rates=self.rates(current))
        mbr.write_atomic(path, mbr.render_html(snapshot))

    def rates(self, snapshot: Mapping[str, Any]) -> list[dict[str, Any]]:
        if self.fx_file is None:
            return snapshot.get("report_fx_rates", [])
        if not self.fx_file.exists():
            LOG.warning("Saved reporting FX file is missing; Combined foreign amounts remain unavailable: %s", self.fx_file)
            return []
        value = json.loads(self.fx_file.read_text(encoding="utf-8"))
        if not isinstance(value, list):
            raise TypeError("Saved reporting FX file must contain a list of dated rate records")
        return value

    def load(self) -> tuple[dict[str, Any], str]:
        content = self.path.read_bytes()
        revision = hashlib.sha256(content).hexdigest()
        return mbr.parse_dashboard(content.decode("utf-8")), revision

    def validate(
        self, path: Path, filename: str, kind: str, basis: str,
        currency: str = "", rate: str = "",
    ) -> dict[str, Any]:
        self.pending = None
        current, revision = self.load()
        digest = uploads.file_digest(path)
        if kind == "workbook":
            if path.suffix.casefold() not in (".xlsb", ".xlsx"):
                raise ValueError("Workbook upload must be .xlsb or .xlsx")
            try:
                import pythoncom
            except ImportError as exc:
                raise ValueError("Workbook uploads require desktop Excel and pywin32 in the server's Python. CSV uploads do not.") from exc
            pythoncom.CoInitialize()
            try:
                with tempfile.TemporaryDirectory(prefix=".mbr-read-", dir=self.path.parent) as directory:
                    extraction = Path(directory)
                    mbr.extract_workbook(path, extraction)
                    incoming = mbr.read_extraction(extraction)
            finally:
                pythoncom.CoUninitialize()
            incoming["meta"]["source_name"] = filename
            incoming["meta"]["sources"][0]["source_name"] = filename
        elif kind == "cost":
            if path.suffix.casefold() != ".csv":
                raise ValueError("Cost-data upload must be a UTF-8 CSV")
            incoming = uploads.cost_csv(path, filename, digest, current, basis, currency, rate)
        elif kind == "advisor":
            if path.suffix.casefold() != ".csv":
                raise ValueError("Advisor upload must be a UTF-8 CSV")
            incoming = uploads.advisor_csv(path, filename, digest, current, basis)
        else:
            raise ValueError("Choose workbook, cost or advisor upload")
        result, preview = uploads.update_report(current, incoming, fx_rates=self.rates(current))
        self.pending = PendingUpload(secrets.token_urlsafe(24), revision, time.monotonic(), result, preview)
        return {"id": self.pending.id, "filename": filename, **preview}

    def commit(self, identity: str, replace: bool) -> dict[str, Any]:
        pending = self.pending
        if not pending or pending.id != identity or time.monotonic() - pending.created > 900:
            raise ReportConflict("The preview expired. Validate the file again before applying it.")
        _current, revision = self.load()
        if revision != pending.revision:
            self.pending = None
            raise ReportConflict("Report data changed after this preview. Reload and validate again.")
        if pending.preview["requires_replacement"] and not replace:
            raise ReportConflict("Confirm replacement of the full account/month scopes shown in the preview.")
        mbr.write_atomic(self.path, mbr.render_html(pending.result))
        self.pending = None
        LOG.info("Committed report upload; retained months=%s", pending.preview["retained_months"])
        return {"applied": True, **pending.preview}


class ReportServer(ThreadingHTTPServer):
    daemon_threads = True
    block_on_close = False

    def __init__(self, address: tuple[str, int], store: ReportStore) -> None:
        self.store = store
        self.token = secrets.token_urlsafe(32)
        super().__init__(address, ReportHandler)
        self.origin = f"http://127.0.0.1:{self.server_port}"


class ReportHandler(BaseHTTPRequestHandler):
    server: ReportServer

    def log_message(self, fmt: str, *args: Any) -> None:
        LOG.info(fmt, *args)

    def reply(self, status: int, content: bytes, kind: str = "application/json") -> None:
        self.send_response(status)
        self.send_header("Content-Type", kind + "; charset=utf-8")
        self.send_header("Content-Length", str(len(content)))
        self.send_header("Cache-Control", "no-store")
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header("Referrer-Policy", "no-referrer")
        self.send_header("Cross-Origin-Resource-Policy", "same-origin")
        self.send_header("X-Frame-Options", "DENY")
        self.end_headers()
        self.wfile.write(content)

    def json(self, status: int, value: Mapping[str, Any]) -> None:
        self.reply(status, json.dumps(value, ensure_ascii=True, allow_nan=False).encode("utf-8"))

    def allowed(self, write: bool = False) -> bool:
        expected = f"127.0.0.1:{self.server.server_port}"
        if self.headers.get("Host") != expected:
            self.close_connection = True
            self.json(403, {"error": "Only this report's explicit loopback Host is allowed."})
            return False
        origin = self.headers.get("Origin")
        if origin is not None and origin != self.server.origin:
            self.close_connection = True
            self.json(403, {"error": "Cross-origin report access is refused."})
            return False
        if write and not secrets.compare_digest(self.headers.get("X-AzFinOps-Report", ""), self.server.token):
            self.close_connection = True
            self.json(403, {"error": "Open the report through this local server before uploading."})
            return False
        return True

    def do_GET(self) -> None:
        if not self.allowed():
            return
        route = urlsplit(self.path).path
        try:
            if route in ("/", "/Azure-MBR-Dashboard.html"):
                with self.server.store.lock:
                    snapshot, _revision = self.server.store.load()
                content = mbr.render_html(snapshot, {
                    "available": True, "token": self.server.token,
                    "origin": self.server.origin, "schema": uploads.UPLOAD_SCHEMA,
                })
                self.reply(200, content.encode("utf-8"), "text/html")
            elif route == "/api/report/info":
                with self.server.store.lock:
                    snapshot, revision = self.server.store.load()
                self.json(200, {"retention": snapshot["retention"], "rows": snapshot["meta"]["source_rows"],
                                "revision": revision, "schema": uploads.UPLOAD_SCHEMA})
            elif route == "/favicon.ico":
                self.reply(204, b"", "image/x-icon")
            else:
                self.json(404, {"error": "Report route not found."})
        except (OSError, ValueError, KeyError) as exc:
            LOG.exception("Cannot read the saved report")
            self.json(500, {"error": f"Cannot read the saved report: {exc}"})

    def body_length(self, limit: int) -> int:
        if self.headers.get("Transfer-Encoding") is not None:
            raise ValueError("Chunked uploads are not supported; send a file with Content-Length")
        value = self.headers.get("Content-Length", "")
        if not value.isdigit() or not 0 < int(value) <= limit:
            raise ValueError(f"Upload must have a positive Content-Length no larger than {limit:,} bytes")
        return int(value)

    def do_POST(self) -> None:
        if not self.allowed(write=True):
            return
        request = urlsplit(self.path)
        if request.path not in ("/api/report/validate", "/api/report/apply"):
            self.close_connection = True
            self.json(404, {"error": "Report action not found."})
            return
        if not self.server.store.lock.acquire(blocking=False):
            self.close_connection = True
            self.json(409, {"error": "Another report update is running. Wait for it to finish."})
            return
        try:
            self.connection.settimeout(180)
            if request.path == "/api/report/validate":
                length = self.body_length(uploads.MAX_UPLOAD_BYTES)
                if self.headers.get("Content-Type", "").split(";")[0] != "application/octet-stream":
                    raise ValueError("Upload the selected file as application/octet-stream")
                filename = unquote(self.headers.get("X-Report-Filename", ""))
                if not filename or len(filename) > 180 or any(character in filename for character in '\\/:<>"|?*\r\n') or filename in (".", ".."):
                    raise ValueError("Upload filename is invalid")
                params = parse_qs(request.query)
                if any(len(values) != 1 for values in params.values()):
                    raise ValueError("Upload options may not be repeated")
                options = {name: values[0] for name, values in params.items()}
                if set(options) - {"kind", "basis", "currency", "rate"}:
                    raise ValueError("Unknown upload option")
                with tempfile.TemporaryDirectory(prefix=".mbr-upload-", dir=self.server.store.path.parent) as directory:
                    path = Path(directory) / filename
                    remaining = length
                    with path.open("wb") as handle:
                        while remaining:
                            chunk = self.rfile.read(min(1024 * 1024, remaining))
                            if not chunk:
                                raise ValueError("Upload ended before all declared bytes arrived")
                            handle.write(chunk)
                            remaining -= len(chunk)
                    result = self.server.store.validate(
                        path, filename, options.get("kind", ""), options.get("basis", ""),
                        options.get("currency", ""), options.get("rate", ""),
                    )
                self.json(200, result)
            else:
                length = self.body_length(4096)
                if self.headers.get("Content-Type", "").split(";")[0] != "application/json":
                    raise ValueError("Apply expects application/json")
                value = json.loads(self.rfile.read(length))
                if not isinstance(value, dict) or set(value) != {"id", "replace"} or not isinstance(value["id"], str) or not isinstance(value["replace"], bool):
                    raise ValueError("Apply requires a preview id and an explicit replacement flag")
                self.json(200, self.server.store.commit(value["id"], value["replace"]))
        except ReportConflict as exc:
            LOG.warning("Report conflict: %s", exc)
            self.close_connection = True
            self.json(409, {"error": str(exc)})
        except (ValueError, KeyError, UnicodeError, csv.Error) as exc:
            LOG.warning("Report upload refused: %s", exc)
            self.close_connection = True
            self.json(422, {"error": str(exc)})
        except Exception as exc:
            LOG.exception("Report update failed")
            self.close_connection = True
            self.json(500, {"error": f"Report update failed. No success is recorded: {exc}"})
        finally:
            self.server.store.lock.release()


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--dashboard", type=Path, default=mbr.ROOT / "data" / "mbr" / "Azure-MBR-Dashboard.html")
    parser.add_argument("--port", type=int, default=8766)
    parser.add_argument("--fx-file", type=Path, default=mbr.ROOT / "data" / "fx_rates.json")
    args = parser.parse_args()
    if not 1024 <= args.port <= 65535:
        parser.error("Choose a local port from 1024 to 65535")
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    store = ReportStore(args.dashboard.resolve(strict=True), args.fx_file.resolve())
    with ReportServer(("127.0.0.1", args.port), store) as server:
        LOG.info("Report uploads: %s | two newest supplied billing months | no main database access", server.origin)
        try:
            server.serve_forever()
        except KeyboardInterrupt:
            LOG.info("Report upload server stopped")


if __name__ == "__main__":
    main()
