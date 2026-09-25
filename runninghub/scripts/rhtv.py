#!/usr/bin/env python3
"""Minimal RHTV Canvas client.

RHTV uses a browser access token and a canvas API that is separate from the
public RunningHub API key endpoints.  The token is intentionally accepted only
through ``RHTV_ACCESS_TOKEN`` so it is less likely to leak through shell
history or process listings.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import subprocess
import sys
import tempfile
import time
from pathlib import Path
from urllib.parse import urlparse


DEFAULT_API_BASE = "https://www.runninghub.ai"
CANVAS_URL_RE = re.compile(r"^/project/canvas/(\d+)(?:/|$)")
SUCCESS_STATES = {"SUCCESS", "SUCCEEDED", "FINISHED", "DONE", "COMPLETED"}
FAILURE_STATES = {"FAILED", "FAILURE", "ERROR", "CANCELED", "CANCELLED", "TIMEOUT"}


class RHTVError(RuntimeError):
    """An expected RHTV client error with a stable machine-readable code."""

    def __init__(self, code: str, message: str, detail=None):
        super().__init__(message)
        self.code = code
        self.message = message
        self.detail = detail


def fail(code: str, message: str, detail=None) -> None:
    payload = {"error": code, "message": message}
    if detail is not None:
        payload["detail"] = detail
    print(json.dumps(payload, ensure_ascii=False), file=sys.stderr)
    raise SystemExit(1)


def parse_canvas_id(value: str) -> str:
    value = str(value or "").strip()
    if value.isdigit():
        return value
    try:
        parsed = urlparse(value)
    except ValueError as exc:
        raise RHTVError("INVALID_CANVAS", "Invalid RHTV canvas URL or ID") from exc
    if parsed.hostname != "rhtv.runninghub.ai":
        raise RHTVError(
            "INVALID_CANVAS",
            "Expected an rhtv.runninghub.ai project/canvas URL or a numeric canvas ID",
        )
    match = CANVAS_URL_RE.match(parsed.path)
    if not match:
        raise RHTVError("INVALID_CANVAS", "RHTV URL does not contain a canvas ID")
    return match.group(1)


def require_token() -> str:
    token = os.environ.get("RHTV_ACCESS_TOKEN", "").strip()
    if not token:
        raise RHTVError(
            "NO_RHTV_TOKEN",
            "Set RHTV_ACCESS_TOKEN in the environment. A RunningHub API key is not an RHTV browser token.",
        )
    return token


def api_base() -> str:
    return os.environ.get("RHTV_API_BASE_URL", DEFAULT_API_BASE).rstrip("/")


def post_json(path: str, payload: dict) -> dict:
    token = require_token()
    with tempfile.NamedTemporaryFile(mode="w", suffix=".json", delete=False, encoding="utf-8") as handle:
        json.dump(payload, handle, ensure_ascii=False)
        body_path = handle.name
    try:
        command = [
            "curl", "-sS", "--fail-with-body", "-X", "POST",
            f"{api_base()}{path}", "--max-time", "60",
            "-H", "Content-Type: application/json",
            "-H", "version: 1.0.0",
            "-H", "X-RH-Lang: en_US",
            "-H", "User-Language: en_US",
            "-H", f"Authorization: Bearer {token}",
            "-d", f"@{body_path}",
        ]
        result = subprocess.run(command, capture_output=True, text=True)
    finally:
        os.unlink(body_path)

    raw = result.stdout or result.stderr
    try:
        response = json.loads(raw)
    except json.JSONDecodeError:
        response = None
    if result.returncode != 0:
        status_message = response.get("msg") if isinstance(response, dict) else raw[:500]
        raise RHTVError("RHTV_HTTP_ERROR", str(status_message or "RHTV request failed"))
    if not isinstance(response, dict):
        raise RHTVError("RHTV_BAD_RESPONSE", "RHTV returned invalid JSON", raw[:500])
    if "code" in response and int(response.get("code") or 0) != 0:
        code = int(response.get("code") or 0)
        error = "RHTV_AUTH_FAILED" if code in {401, 403, 412} else "RHTV_API_ERROR"
        raise RHTVError(error, str(response.get("msg") or "RHTV request failed"), {"code": code})
    data = response.get("data", response)
    return data if isinstance(data, dict) else {"data": data}


def get_canvas_detail(canvas_id: str) -> dict:
    return post_json("/canvas/getCanvasDetail", {"id": canvas_id})


def _json_object(value):
    if isinstance(value, dict):
        return value
    if isinstance(value, str):
        try:
            parsed = json.loads(value)
        except json.JSONDecodeError:
            return None
        return parsed if isinstance(parsed, dict) else None
    return None


def extract_graph(detail: dict) -> dict:
    queue = [detail]
    seen = set()
    while queue:
        current = queue.pop(0)
        if id(current) in seen:
            continue
        seen.add(id(current))
        if isinstance(current.get("nodes"), list):
            return {
                "nodes": current.get("nodes", []),
                "edges": current.get("edges", []),
            }
        for key in ("canvas", "canvasContent", "canvas_content", "content", "snapshot", "data"):
            nested = _json_object(current.get(key))
            if nested is not None:
                queue.append(nested)
    raise RHTVError("CANVAS_GRAPH_MISSING", "The RHTV response did not contain a canvas graph")


def node_label(node: dict) -> str:
    data = node.get("data") if isinstance(node.get("data"), dict) else {}
    return str(data.get("label") or data.get("title") or data.get("name") or node.get("type") or "")


def summarize_canvas(canvas_id: str, detail: dict, graph: dict) -> dict:
    nodes = []
    for node in graph["nodes"]:
        if not isinstance(node, dict):
            continue
        data = node.get("data") if isinstance(node.get("data"), dict) else {}
        nodes.append({
            "id": str(node.get("id", "")),
            "type": node.get("type"),
            "label": node_label(node),
            "modelCode": data.get("modelCode") or data.get("rhModel"),
            "status": data.get("status"),
        })
    return {
        "canvasId": canvas_id,
        "name": detail.get("name") or detail.get("title"),
        "nodeCount": len(graph["nodes"]),
        "edgeCount": len(graph["edges"]),
        "nodes": nodes,
    }


def parse_value(value: str):
    try:
        return json.loads(value)
    except json.JSONDecodeError:
        return value


def set_path(target: dict, path: str, value) -> None:
    parts = [part for part in path.split(".") if part]
    if parts and parts[0] == "data":
        parts = parts[1:]
    if not parts:
        raise RHTVError("INVALID_OVERRIDE", "Override field path cannot be empty")
    cursor = target.setdefault("data", {})
    if not isinstance(cursor, dict):
        raise RHTVError("INVALID_OVERRIDE", "Target node data is not an object")
    for part in parts[:-1]:
        child = cursor.get(part)
        if child is None:
            child = {}
            cursor[part] = child
        if not isinstance(child, dict):
            raise RHTVError("INVALID_OVERRIDE", f"Override path crosses a non-object field: {path}")
        cursor = child
    cursor[parts[-1]] = value


def apply_overrides(graph: dict, overrides: list[str]) -> None:
    by_id = {str(node.get("id")): node for node in graph["nodes"] if isinstance(node, dict)}
    for item in overrides:
        match = re.match(r"^([^:]+):([^=]+)=(.*)$", item, re.DOTALL)
        if not match:
            raise RHTVError("INVALID_OVERRIDE", f"Expected NODE_ID:field.path=value, got: {item}")
        node_id, field_path, value = match.groups()
        node = by_id.get(node_id)
        if node is None:
            raise RHTVError("NODE_NOT_FOUND", f"Node {node_id} was not found in the canvas")
        set_path(node, field_path, parse_value(value))


def recursive_find(value, keys: set[str]):
    if isinstance(value, dict):
        for key, child in value.items():
            if key in keys and child not in (None, ""):
                return child
            found = recursive_find(child, keys)
            if found not in (None, ""):
                return found
    elif isinstance(value, list):
        for child in value:
            found = recursive_find(child, keys)
            if found not in (None, ""):
                return found
    return None


def task_id_from(response: dict) -> str | None:
    value = recursive_find(response, {"taskId", "task_id"})
    return str(value) if value not in (None, "") else None


def run_node(canvas_id: str, node_id: str, overrides: list[str]) -> dict:
    detail = get_canvas_detail(canvas_id)
    graph = extract_graph(detail)
    if not any(str(node.get("id")) == node_id for node in graph["nodes"] if isinstance(node, dict)):
        raise RHTVError("NODE_NOT_FOUND", f"Node {node_id} was not found in canvas {canvas_id}")
    apply_overrides(graph, overrides)
    payload = {
        "canvasId": canvas_id,
        "targetType": "NODE",
        "targetId": node_id,
        "canvas": graph,
    }
    name = detail.get("name") or detail.get("title")
    if name:
        payload["name"] = name
    response = post_json("/canvas/task/run", payload)
    task_id = task_id_from(response)
    if not task_id:
        raise RHTVError("MISSING_TASK_ID", "RHTV accepted the request but returned no taskId", response)
    return {"canvasId": canvas_id, "nodeId": node_id, "taskId": task_id, "response": response}


def task_status(task_ids: list[str], include_results: bool = True) -> dict:
    return post_json(
        "/canvas/task/batchStatus",
        {"taskIds": [str(value) for value in task_ids], "includeResults": include_results},
    )


def status_name(value: dict) -> str:
    status = recursive_find(value, {"status", "taskStatus", "state"})
    return str(status or "").strip().upper()


def wait_for_task(task_id: str, timeout: int, poll_interval: int) -> dict:
    started = time.monotonic()
    latest = {}
    while time.monotonic() - started < timeout:
        latest = task_status([task_id], include_results=True)
        state = status_name(latest)
        if state in SUCCESS_STATES:
            return latest
        if state in FAILURE_STATES:
            raise RHTVError("TASK_FAILED", f"RHTV task ended with status {state}", latest)
        time.sleep(poll_interval)
    raise RHTVError("TASK_TIMEOUT", f"RHTV task did not finish within {timeout} seconds", latest)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Inspect and run RHTV Canvas projects")
    actions = parser.add_mutually_exclusive_group(required=True)
    actions.add_argument("--info", metavar="CANVAS_URL_OR_ID", help="Show canvas nodes")
    actions.add_argument("--run-node", nargs=2, metavar=("CANVAS_URL_OR_ID", "NODE_ID"))
    actions.add_argument("--status", nargs="+", metavar="TASK_ID")
    actions.add_argument("--wait", metavar="TASK_ID")
    actions.add_argument("--cancel", metavar="TASK_ID")
    parser.add_argument("--set", action="append", default=[], metavar="NODE_ID:FIELD=VALUE")
    parser.add_argument("--raw", action="store_true", help="Include the full canvas detail for --info")
    parser.add_argument("--timeout", type=int, default=1200)
    parser.add_argument("--poll-interval", type=int, default=5)
    return parser


def main() -> None:
    args = build_parser().parse_args()
    try:
        if args.info:
            canvas_id = parse_canvas_id(args.info)
            detail = get_canvas_detail(canvas_id)
            graph = extract_graph(detail)
            result = summarize_canvas(canvas_id, detail, graph)
            if args.raw:
                result["detail"] = detail
        elif args.run_node:
            canvas_id = parse_canvas_id(args.run_node[0])
            result = run_node(canvas_id, str(args.run_node[1]), args.set)
        elif args.status:
            result = task_status(args.status, include_results=True)
        elif args.wait:
            result = wait_for_task(args.wait, args.timeout, args.poll_interval)
        else:
            result = post_json("/canvas/task/cancel", {"taskId": str(args.cancel)})
        print(json.dumps(result, ensure_ascii=False, indent=2))
    except RHTVError as exc:
        fail(exc.code, exc.message, exc.detail)


if __name__ == "__main__":
    main()
