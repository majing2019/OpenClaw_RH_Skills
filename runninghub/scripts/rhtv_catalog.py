#!/usr/bin/env python3
"""Read the public RHTV common-workflow catalog in a compact form.

This is a read-only discovery client. It does not use browser session tokens,
clone workflows, create canvases, or submit paid generation tasks.
"""

from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
import tempfile
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from urllib.parse import urlparse


DEFAULT_API_BASE = "https://www.runninghub.ai"
LIST_PATH = "/canvas/common-workflow/list"
DETAIL_PATH = "/canvas/admin/common-workflow/detail"
MAX_PAGE_SIZE = 100


class CatalogError(RuntimeError):
    pass


def api_base() -> str:
    return os.environ.get("RHTV_CATALOG_API_BASE_URL", DEFAULT_API_BASE).rstrip("/")


def api_host() -> str:
    return urlparse(api_base()).netloc


def post_json(path: str, payload: dict, timeout: int = 90) -> dict:
    with tempfile.NamedTemporaryFile(mode="w", suffix=".json", delete=False, encoding="utf-8") as handle:
        json.dump(payload, handle, ensure_ascii=False)
        body_path = handle.name
    try:
        result = subprocess.run(
            [
                "curl", "-sS", "--fail-with-body", "-X", "POST",
                f"{api_base()}{path}", "--max-time", str(timeout),
                "-H", "Content-Type: application/json",
                "-H", f"Host: {api_host()}",
                "-d", f"@{body_path}",
            ],
            capture_output=True,
            text=True,
        )
    finally:
        os.unlink(body_path)

    raw = result.stdout or result.stderr
    try:
        response = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise CatalogError("RHTV catalog returned invalid JSON") from exc
    if result.returncode != 0:
        raise CatalogError(str(response.get("msg") or "RHTV catalog request failed"))
    if int(response.get("code") or 0) != 0:
        raise CatalogError(str(response.get("msg") or "RHTV catalog request failed"))
    data = response.get("data")
    if not isinstance(data, dict):
        raise CatalogError("RHTV catalog returned an unexpected response")
    return data


def graph_from(record: dict) -> dict:
    graph = record.get("workflowContent") or {}
    if isinstance(graph, str):
        try:
            graph = json.loads(graph)
        except json.JSONDecodeError:
            return {}
    return graph if isinstance(graph, dict) else {}


def media_type(url: str) -> str:
    suffix = Path(urlparse(url).path).suffix.lower()
    if suffix in {".mp4", ".mov", ".webm", ".m4v"}:
        return "video"
    if suffix in {".jpg", ".jpeg", ".png", ".webp", ".gif", ".avif"}:
        return "image"
    return "unknown"


def output_types(nodes: list[dict]) -> list[str]:
    found = set()
    for node in nodes:
        if not isinstance(node, dict) or node.get("type") == "group":
            continue
        node_type = str(node.get("type") or "").lower()
        data = node.get("data") if isinstance(node.get("data"), dict) else {}
        sub_type = str(data.get("subType") or "").lower()
        kinds = ("image", "video", "audio", "text", "3d")
        node_kinds = [kind for kind in kinds if kind in node_type]
        if not node_kinds:
            # Subtypes commonly describe a conversion such as text-image or
            # image-video. The last matching token is the produced media type.
            node_kinds = [kind for kind in kinds if sub_type.endswith(kind)]
        found.update("string" if kind == "text" else kind for kind in node_kinds)
    return sorted(found)


def normalize_record(record: dict, include_nodes: bool = False) -> dict:
    graph = graph_from(record)
    raw_nodes = graph.get("nodes") if isinstance(graph.get("nodes"), list) else []
    nodes = [node for node in raw_nodes if isinstance(node, dict) and node.get("type") != "group"]
    edges = graph.get("edges") if isinstance(graph.get("edges"), list) else []
    models = sorted({
        str(node.get("data", {}).get("modelCode"))
        for node in nodes
        if isinstance(node.get("data"), dict) and node.get("data", {}).get("modelCode")
    })
    thumbnail = str(record.get("thumbnail") or "")
    item = {
        "id": str(record.get("id") or ""),
        "name": str(record.get("name") or "未命名工作流"),
        "description": str(record.get("description") or ""),
        "thumbnail": thumbnail if thumbnail.startswith(("https://", "http://")) else "",
        "previewType": media_type(thumbnail),
        "updateTime": record.get("updateTime"),
        "nodeCount": len(nodes),
        "edgeCount": len(edges),
        "outputTypes": output_types(nodes),
        "models": models,
    }
    if include_nodes:
        item["nodes"] = [
            {
                "id": str(node.get("id") or ""),
                "type": node.get("type"),
                "label": str((node.get("data") or {}).get("label") or (node.get("data") or {}).get("title") or ""),
                "subType": (node.get("data") or {}).get("subType"),
                "modelCode": (node.get("data") or {}).get("modelCode"),
            }
            for node in nodes
        ]
    return item


def list_page(page: int, size: int, keyword: str = "") -> dict:
    payload: dict = {"page": page, "size": min(max(size, 1), MAX_PAGE_SIZE)}
    if keyword:
        payload["keyword"] = keyword
    return post_json(LIST_PATH, payload)


def list_all(keyword: str = "") -> dict:
    first = list_page(1, MAX_PAGE_SIZE, keyword)
    records = list(first.get("records") or [])
    pages = max(1, int(first.get("pages") or 1))
    if pages > 1:
        with ThreadPoolExecutor(max_workers=min(4, pages - 1)) as executor:
            results = executor.map(lambda page: list_page(page, MAX_PAGE_SIZE, keyword), range(2, pages + 1))
            for result in results:
                records.extend(result.get("records") or [])
    workflows = [normalize_record(record) for record in records if isinstance(record, dict)]
    return {
        "source": "RHTV live catalog",
        "total": int(first.get("total") or len(workflows)),
        "count": len(workflows),
        "workflows": workflows,
    }


def get_detail(workflow_id: str) -> dict:
    normalized = str(workflow_id or "").strip()
    if not normalized.isdigit():
        raise CatalogError("RHTV workflow ID must be numeric")
    data = post_json(DETAIL_PATH, {"id": normalized})
    return normalize_record(data, include_nodes=True)


def main() -> int:
    parser = argparse.ArgumentParser(description="Browse the live public RHTV workflow catalog")
    mode = parser.add_mutually_exclusive_group(required=True)
    mode.add_argument("--list", action="store_true", help="List every public RHTV workflow")
    mode.add_argument("--info", metavar="RHTV_WORKFLOW_ID", help="Show compact workflow details")
    parser.add_argument("--search", default="", help="Server-side catalog search")
    args = parser.parse_args()
    try:
        payload = list_all(args.search.strip()) if args.list else get_detail(args.info)
        print(json.dumps(payload, ensure_ascii=False, indent=2))
        return 0
    except CatalogError as exc:
        print(json.dumps({"error": "RHTV_CATALOG_ERROR", "message": str(exc)}, ensure_ascii=False), file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
