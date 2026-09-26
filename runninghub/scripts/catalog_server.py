#!/usr/bin/env python3
"""Local web catalog for the RunningHub skill.

The server binds to localhost, serves the bundled HTML, and delegates live AI
Application and Workflow API requests to their existing helpers. API keys stay
in the local process/config and are never returned to the browser.
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
WORKFLOW_SCRIPT = SCRIPT_DIR / "runninghub_workflow.py"
COVER_DIR = Path("/tmp/openclaw/rh-output/app_covers")
ALLOWED_COVER_SUFFIXES = {".jpg", ".jpeg", ".png", ".webp", ".gif"}
APP_SORTS = {"RECOMMEND", "HOTTEST", "NEWEST"}

sys.path.insert(0, str(SCRIPT_DIR))
from runninghub import resolve_api_key  # noqa: E402


class CatalogState:
    def __init__(self):
        self.app_cache: dict[tuple, tuple[float, dict]] = {}

    def get_apps(self, sort: str, size: int, page: int, days: int) -> dict:
        cache_key = (sort, size, page, days)
        cached = self.app_cache.get(cache_key)
        if cached and time.monotonic() - cached[0] < 60:
            return cached[1]
        args = [
            sys.executable, str(APP_SCRIPT), "--list", "--sort", sort,
            "--size", str(size), "--page", str(page), "--days", str(days),
        ]
        data = run_json_command(args, timeout=90)
        for app in data.get("apps", []):
            cover_file = app.pop("coverFile", None)
            if cover_file:
                app["coverUrl"] = f"/api/covers/{Path(cover_file).name}"
        self.app_cache[cache_key] = (time.monotonic(), data)
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
                self.send_json(STATE.get_apps(sort, size, page, days))
                return
            if parsed.path.startswith("/api/apps/"):
                webapp_id = parsed.path.removeprefix("/api/apps/")
                if not webapp_id.isdigit():
                    raise ValueError("Invalid AI Application ID")
                data = run_json_command(
                    [sys.executable, str(APP_SCRIPT), "--info", webapp_id],
                    timeout=60,
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
