#!/usr/bin/env python3
"""Local web catalog for the RunningHub skill.

The server binds to localhost, serves the bundled HTML, and delegates live AI
Application requests to its existing helper, and reads the public RHTV
workflow catalog through a separate read-only helper. API keys stay in the
local process/config and are never returned to the browser.
"""

from __future__ import annotations

import argparse
import errno
import json
import mimetypes
import subprocess
import sys
import time
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, unquote, urlparse


SCRIPT_DIR = Path(__file__).resolve().parent
SKILL_DIR = SCRIPT_DIR.parent
HTML_PATH = SKILL_DIR / "web" / "index.html"
CAPABILITIES_PATH = SKILL_DIR / "data" / "capabilities.json"
APP_SCRIPT = SCRIPT_DIR / "runninghub_app.py"
AI_APP_CATALOG_SCRIPT = SCRIPT_DIR / "ai_app_catalog.py"
WORKFLOW_SCRIPT = SCRIPT_DIR / "runninghub_workflow.py"
RHTV_CATALOG_SCRIPT = SCRIPT_DIR / "rhtv_catalog.py"
COVER_DIR = SKILL_DIR / "data" / "ai_app_covers"
ALLOWED_COVER_SUFFIXES = {".jpg", ".jpeg", ".png", ".webp", ".gif", ".mp4", ".webm", ".mov"}
APP_SORTS = {"RECOMMEND", "HOTTEST", "NEWEST"}

sys.path.insert(0, str(SCRIPT_DIR))
from runninghub import resolve_api_key  # noqa: E402


class CatalogState:
    def __init__(self):
        self.app_cache: dict[tuple, tuple[float, dict]] = {}
        self.rhtv_cache: tuple[float, dict] | None = None

    def get_apps(self, sort: str, size: int, page: int, days: int, force: bool = False) -> dict:
        cache_key = (sort, size, page, days)
        cached = self.app_cache.get(cache_key)
        if cached and not force and time.monotonic() - cached[0] < 60:
            return cached[1]
        args = [
            sys.executable, str(AI_APP_CATALOG_SCRIPT), "--list", "--sort", sort,
            "--size", str(size), "--page", str(page), "--days", str(days),
        ]
        # The local database is the source for normal reads. A refresh first
        # compares this page with RunningHub and writes only changed records.
        if force or not cached:
            sync_args = [
                sys.executable, str(AI_APP_CATALOG_SCRIPT), "--sync", "--sort", sort,
                "--size", str(size), "--page", str(page), "--days", str(days),
            ]
            synced = run_json_command(sync_args, timeout=180)
            data = run_json_command(args, timeout=30)
            data["sync"] = synced.get("sync")
        else:
            data = run_json_command(args, timeout=30)
        for app in data.get("apps", []):
            cover_file = app.pop("coverFile", None)
            if cover_file and Path(cover_file).is_file() and Path(cover_file).parent == COVER_DIR:
                app["coverUrl"] = f"/api/covers/{Path(cover_file).name}"
            elif app.get("coverUrl"):
                # Older rows may still point at a removed temporary file. Keep
                # the public RunningHub cover as a safe fallback until the row
                # is refreshed into the stable local cover directory.
                app["coverUrl"] = app["coverUrl"]
            else:
                app.pop("coverUrl", None)
        self.app_cache[cache_key] = (time.monotonic(), data)
        return data

    def get_rhtv_workflows(self, force: bool = False) -> dict:
        if not force and self.rhtv_cache and time.monotonic() - self.rhtv_cache[0] < 60:
            return self.rhtv_cache[1]
        data = run_json_command(
            [sys.executable, str(RHTV_CATALOG_SCRIPT), "--sync" if force else "--list"],
            timeout=180,
        )
        self.rhtv_cache = (time.monotonic(), data)
        return data


STATE = CatalogState()


def run_json_command(args: list[str], timeout: int) -> dict:
    result = subprocess.run(args, capture_output=True, text=True, timeout=timeout)
    output = result.stdout.strip()
    if result.returncode != 0:
        error_text = result.stderr.strip() or output or "Command failed"
        try:
            detail = json.loads(error_text.splitlines()[-1])
            message = detail.get("message", error_text)
        except (json.JSONDecodeError, AttributeError):
            message = error_text[-1000:]
        raise RuntimeError(message)
    try:
        payload = json.loads(output)
    except json.JSONDecodeError as exc:
        raise RuntimeError("RunningHub helper returned invalid JSON") from exc
    if not isinstance(payload, dict):
        raise RuntimeError("RunningHub helper returned an unexpected response")
    return payload


class CatalogHandler(BaseHTTPRequestHandler):
    server_version = "RunningHubCatalog/1.0"

    def log_message(self, format_string, *args):
        print(f"[catalog] {self.address_string()} - {format_string % args}", file=sys.stderr)

    def send_json(self, payload: dict, status: HTTPStatus = HTTPStatus.OK):
        body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.send_header("X-Content-Type-Options", "nosniff")
        self.end_headers()
        self.wfile.write(body)

    def send_file(self, path: Path, content_type: str | None = None):
        if not path.is_file():
            self.send_error(HTTPStatus.NOT_FOUND)
            return
        body = path.read_bytes()
        self.send_response(HTTPStatus.OK)
        self.send_header("Content-Type", content_type or mimetypes.guess_type(path.name)[0] or "application/octet-stream")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header("Cache-Control", "no-cache")
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self):
        parsed = urlparse(self.path)
        try:
            if parsed.path in {"/", "/index.html"}:
                self.send_file(HTML_PATH, "text/html; charset=utf-8")
                return
            if parsed.path == "/api/status":
                catalog = json.loads(CAPABILITIES_PATH.read_text(encoding="utf-8"))
                api_configured = bool(resolve_api_key(None))
                self.send_json({
                    "catalogVersion": catalog.get("version"),
                    "total": catalog.get("total", 0),
                    "apiConfigured": api_configured,
                    "workflowConfigured": api_configured,
                })
                return
            if parsed.path == "/api/capabilities":
                self.send_file(CAPABILITIES_PATH, "application/json; charset=utf-8")
                return
            if parsed.path == "/api/apps":
                query = parse_qs(parsed.query)
                sort = query.get("sort", ["RECOMMEND"])[0].upper()
                if sort not in APP_SORTS:
                    raise ValueError("sort must be RECOMMEND, HOTTEST, or NEWEST")
                size = max(1, min(30, int(query.get("size", ["12"])[0])))
                page = max(1, int(query.get("page", ["1"])[0]))
                days = max(1, min(30, int(query.get("days", ["7"])[0])))
                force = query.get("refresh", ["0"])[0] == "1"
                self.send_json(STATE.get_apps(sort, size, page, days, force=force))
                return
            if parsed.path.startswith("/api/apps/"):
                webapp_id = parsed.path.removeprefix("/api/apps/")
                if not webapp_id.isdigit():
                    raise ValueError("Invalid AI Application ID")
                data = run_json_command(
                    [sys.executable, str(AI_APP_CATALOG_SCRIPT), "--get", webapp_id],
                    timeout=30,
                )
                self.send_json(data)
                return
            if parsed.path.startswith("/api/workflows/"):
                workflow_id = parsed.path.removeprefix("/api/workflows/")
                if not workflow_id.isdigit():
                    raise ValueError("Invalid workflow ID")
                data = run_json_command(
                    [sys.executable, str(WORKFLOW_SCRIPT), "--info", workflow_id],
                    timeout=60,
                )
                self.send_json(data)
                return
            if parsed.path == "/api/rhtv/workflows":
                query = parse_qs(parsed.query)
                force = query.get("refresh", ["0"])[0] == "1"
                self.send_json(STATE.get_rhtv_workflows(force=force))
                return
            if parsed.path.startswith("/api/rhtv/workflows/"):
                rhtv_workflow_id = parsed.path.removeprefix("/api/rhtv/workflows/")
                if not rhtv_workflow_id.isdigit():
                    raise ValueError("Invalid RHTV workflow ID")
                data = run_json_command(
                    [sys.executable, str(RHTV_CATALOG_SCRIPT), "--info", rhtv_workflow_id],
                    timeout=90,
                )
                self.send_json(data)
                return
            if parsed.path.startswith("/api/covers/"):
                name = unquote(parsed.path.removeprefix("/api/covers/"))
                if not name or Path(name).name != name or Path(name).suffix.lower() not in ALLOWED_COVER_SUFFIXES:
                    self.send_error(HTTPStatus.BAD_REQUEST)
                    return
                self.send_file(COVER_DIR / name)
                return
            self.send_error(HTTPStatus.NOT_FOUND)
        except (ValueError, RuntimeError, subprocess.TimeoutExpired) as exc:
            status = HTTPStatus.BAD_REQUEST if isinstance(exc, ValueError) else HTTPStatus.BAD_GATEWAY
            self.send_json({"error": type(exc).__name__, "message": str(exc)}, status)
        except Exception as exc:  # Keep request failures from terminating the local server.
            self.send_json({"error": "CatalogError", "message": str(exc)}, HTTPStatus.INTERNAL_SERVER_ERROR)


def main():
    parser = argparse.ArgumentParser(description="Serve the RunningHub capability catalog")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, help="Local port (default: first available from 8765)")
    args = parser.parse_args()
    if args.host not in {"127.0.0.1", "localhost", "::1"}:
        print("Refusing to expose a credential-aware catalog on a non-loopback host", file=sys.stderr)
        return 2
    requested_port = args.port
    candidates = [requested_port] if requested_port is not None else range(8765, 8786)
    server = None
    selected_port = None
    for port in candidates:
        try:
            server = ThreadingHTTPServer((args.host, port), CatalogHandler)
            selected_port = port
            break
        except OSError as exc:
            if requested_port is not None or exc.errno != errno.EADDRINUSE:
                raise
    if server is None or selected_port is None:
        print("No available local port found from 8765 through 8785", file=sys.stderr)
        return 2
    print(f"RunningHub catalog: http://{args.host}:{selected_port}")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
