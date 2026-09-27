#!/usr/bin/env python3
"""Browse and incrementally cache the public RHTV common-workflow catalog."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import sqlite3
import subprocess
import sys
import tempfile
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import urlparse

DEFAULT_API_BASE = "https://www.runninghub.ai"
LIST_PATH = "/canvas/common-workflow/list"
DETAIL_PATH = "/canvas/admin/common-workflow/detail"
MAX_PAGE_SIZE = 100
RHTV_LIBRARY_URL = "https://rhtv.runninghub.ai/projects/canvas/inspiration/create"
DEFAULT_DB_PATH = Path(__file__).resolve().parents[1] / "data" / "rhtv_catalog.sqlite3"
MEDIA_NAMES = {"image": "图片", "video": "视频", "audio": "音频", "string": "文本", "3d": "3D 模型", "unknown": "内容"}


class CatalogError(RuntimeError):
    pass


def api_base() -> str:
    return os.environ.get("RHTV_CATALOG_API_BASE_URL", DEFAULT_API_BASE).rstrip("/")


def api_host() -> str:
    return urlparse(api_base()).netloc


def database_path() -> Path:
    configured = os.environ.get("RHTV_CATALOG_DB_PATH")
    return Path(configured).expanduser() if configured else DEFAULT_DB_PATH


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def post_json(path: str, payload: dict, timeout: int = 90) -> dict:
    with tempfile.NamedTemporaryFile(mode="w", suffix=".json", delete=False, encoding="utf-8") as handle:
        json.dump(payload, handle, ensure_ascii=False)
        body_path = handle.name
    try:
        result = subprocess.run(
            ["curl", "-sS", "--fail-with-body", "-X", "POST", f"{api_base()}{path}",
             "--max-time", str(timeout), "-H", "Content-Type: application/json",
             "-H", f"Host: {api_host()}", "-d", f"@{body_path}"],
            capture_output=True, text=True,
        )
    finally:
        os.unlink(body_path)
    raw = result.stdout or result.stderr
    try:
        response = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise CatalogError("RHTV catalog returned invalid JSON") from exc
    if result.returncode != 0 or int(response.get("code") or 0) != 0:
        raise CatalogError(str(response.get("msg") or "RHTV catalog request failed"))
    data = response.get("data")
    if not isinstance(data, dict):
        raise CatalogError("RHTV catalog returned an unexpected response")
    return data


def open_database() -> sqlite3.Connection:
    path = database_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    connection = sqlite3.connect(path, timeout=15)
    connection.row_factory = sqlite3.Row
    connection.execute("PRAGMA journal_mode=WAL")
    connection.execute("PRAGMA busy_timeout=15000")
    connection.executescript("""
        CREATE TABLE IF NOT EXISTS rhtv_workflows (
            id TEXT PRIMARY KEY,
            payload_json TEXT NOT NULL,
            content_hash TEXT NOT NULL,
            remote_update_time TEXT,
            first_seen_at TEXT NOT NULL,
            changed_at TEXT NOT NULL,
            active INTEGER NOT NULL DEFAULT 1
        );
        CREATE INDEX IF NOT EXISTS idx_rhtv_workflows_active_update
            ON rhtv_workflows(active, remote_update_time DESC);
        CREATE TABLE IF NOT EXISTS rhtv_sync_runs (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            synced_at TEXT NOT NULL,
            remote_total INTEGER NOT NULL,
            created_count INTEGER NOT NULL,
            updated_count INTEGER NOT NULL,
            unchanged_count INTEGER NOT NULL,
            deactivated_count INTEGER NOT NULL
        );
    """)
    return connection


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


def node_media_type(node: dict) -> str:
    data = node.get("data") if isinstance(node.get("data"), dict) else {}
    node_type = str(node.get("type") or "").lower()
    sub_type = str(data.get("subType") or "").lower()
    for kind in ("image", "video", "audio", "text", "3d"):
        if kind in node_type:
            return "string" if kind == "text" else kind
    for kind in ("image", "video", "audio", "text", "3d"):
        if sub_type.endswith(kind):
            return "string" if kind == "text" else kind
    return "unknown"


def compact_node(node: dict, direction: str = "") -> dict:
    data = node.get("data") if isinstance(node.get("data"), dict) else {}
    kind = node_media_type(node)
    original_label = str(data.get("label") or data.get("title") or "").strip()
    direction_name = "输入" if direction == "input" else "输出" if direction == "output" else "节点"
    label = f"{MEDIA_NAMES[kind]}{direction_name}"
    description = f"工作流的{label}"
    if original_label:
        description += f"（原节点：{original_label}）"
    return {
        "id": str(node.get("id") or ""), "type": kind, "label": label,
        "description": description, "nodeType": node.get("type"),
        "subType": data.get("subType"), "modelCode": data.get("modelCode"),
    }


def graph_io(nodes: list[dict], edges: list[dict]) -> tuple[list[dict], list[dict]]:
    node_ids = {str(node.get("id") or "") for node in nodes}
    incoming = {str(edge.get("target") or "") for edge in edges if isinstance(edge, dict)} & node_ids
    outgoing = {str(edge.get("source") or "") for edge in edges if isinstance(edge, dict)} & node_ids
    inputs = [compact_node(node, "input") for node in nodes if str(node.get("id") or "") not in incoming]
    outputs = [compact_node(node, "output") for node in nodes if str(node.get("id") or "") not in outgoing]
    return inputs, outputs


def chinese_description(inputs: list[dict], outputs: list[dict], node_count: int, models: list[str]) -> str:
    input_names = "、".join(dict.fromkeys(item["label"] for item in inputs)) or "未识别输入"
    output_names = "、".join(dict.fromkeys(item["label"] for item in outputs)) or "未识别输出"
    output_types = {item["type"] for item in outputs}
    purpose = "多媒体创作"
    if output_types == {"video"}:
        purpose = "视频创作"
    elif output_types == {"image"}:
        purpose = "图像创作"
    elif output_types == {"audio"}:
        purpose = "音频创作"
    elif output_types == {"string"}:
        purpose = "文本处理"
    text = f"这是一个 RHTV {purpose}工作流，以{input_names}作为起点，经过 {node_count} 个处理节点，生成{output_names}。"
    if models:
        text += f" 涉及模型：{'、'.join(models[:3])}{' 等' if len(models) > 3 else ''}。"
    return text


def normalize_record(record: dict, include_nodes: bool = False) -> dict:
    graph = graph_from(record)
    raw_nodes = graph.get("nodes") if isinstance(graph.get("nodes"), list) else []
    nodes = [node for node in raw_nodes if isinstance(node, dict) and node.get("type") != "group"]
    edges = [edge for edge in (graph.get("edges") or []) if isinstance(edge, dict)]
    models = sorted({str(node.get("data", {}).get("modelCode")) for node in nodes
                     if isinstance(node.get("data"), dict) and node.get("data", {}).get("modelCode")})
    inputs, outputs = graph_io(nodes, edges)
    thumbnail = str(record.get("thumbnail") or "")
    item = {
        "id": str(record.get("id") or ""), "code": str(record.get("code") or ""),
        "name": str(record.get("name") or "未命名工作流"),
        "description": str(record.get("description") or ""),
        "chineseDescription": chinese_description(inputs, outputs, len(nodes), models),
        "thumbnail": thumbnail if thumbnail.startswith(("https://", "http://")) else "",
        "previewType": media_type(thumbnail), "updateTime": record.get("updateTime"),
        "nodeCount": len(nodes), "edgeCount": len(edges),
        "outputTypes": sorted({entry["type"] for entry in outputs if entry["type"] != "unknown"}),
        "models": models, "inputs": inputs, "outputs": outputs,
        "detailPath": f"/?rhtv={record.get('id')}", "sourceUrl": RHTV_LIBRARY_URL,
    }
    if include_nodes:
        item["nodes"] = [compact_node(node) for node in nodes]
    return item


def record_hash(record: dict) -> str:
    stable = {key: record.get(key) for key in
              ("id", "code", "name", "description", "thumbnail", "updateTime", "workflowContent")}
    encoded = json.dumps(stable, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def list_page(page: int, size: int) -> dict:
    return post_json(LIST_PATH, {"page": page, "size": min(max(size, 1), MAX_PAGE_SIZE)})


def fetch_all_records() -> tuple[list[dict], int]:
    first = list_page(1, MAX_PAGE_SIZE)
    records = list(first.get("records") or [])
    pages = max(1, int(first.get("pages") or 1))
    if pages > 1:
        with ThreadPoolExecutor(max_workers=min(4, pages - 1)) as executor:
            for result in executor.map(lambda page: list_page(page, MAX_PAGE_SIZE), range(2, pages + 1)):
                records.extend(result.get("records") or [])
    return [record for record in records if isinstance(record, dict)], int(first.get("total") or len(records))


def latest_sync(connection: sqlite3.Connection) -> dict | None:
    row = connection.execute("SELECT * FROM rhtv_sync_runs ORDER BY id DESC LIMIT 1").fetchone()
    if not row:
        return None
    return {"syncedAt": row["synced_at"], "remoteTotal": row["remote_total"],
            "created": row["created_count"], "updated": row["updated_count"],
            "unchanged": row["unchanged_count"], "deactivated": row["deactivated_count"]}


def read_cached(connection: sqlite3.Connection, keyword: str = "") -> dict:
    rows = connection.execute(
        "SELECT payload_json FROM rhtv_workflows WHERE active=1 ORDER BY remote_update_time DESC, id DESC"
    ).fetchall()
    workflows = [json.loads(row["payload_json"]) for row in rows]
    normalized_keyword = keyword.casefold().strip()
    if normalized_keyword:
        workflows = [item for item in workflows if normalized_keyword in " ".join([
            item.get("name", ""), item.get("description", ""), item.get("chineseDescription", ""),
            *(item.get("models") or []), *(item.get("outputTypes") or []),
        ]).casefold()]
    return {"source": "RHTV public catalog · local incremental cache", "total": len(workflows),
            "count": len(workflows), "database": str(database_path()),
            "sync": latest_sync(connection), "workflows": workflows}


def sync_catalog(keyword: str = "") -> dict:
    records, remote_total = fetch_all_records()
    timestamp = now_iso()
    created = updated = unchanged = 0
    remote_ids: set[str] = set()
    with open_database() as connection:
        existing = {row["id"]: (row["content_hash"], row["active"])
                    for row in connection.execute("SELECT id,content_hash,active FROM rhtv_workflows")}
        for record in records:
            workflow_id = str(record.get("id") or "")
            if not workflow_id:
                continue
            remote_ids.add(workflow_id)
            digest = record_hash(record)
            previous = existing.get(workflow_id)
            if previous and previous[0] == digest and previous[1] == 1:
                unchanged += 1
                continue
            payload = normalize_record(record, include_nodes=True)
            serialized = json.dumps(payload, ensure_ascii=False, separators=(",", ":"))
            if previous:
                connection.execute(
                    "UPDATE rhtv_workflows SET payload_json=?,content_hash=?,remote_update_time=?,changed_at=?,active=1 WHERE id=?",
                    (serialized, digest, record.get("updateTime"), timestamp, workflow_id))
                updated += 1
            else:
                connection.execute(
                    "INSERT INTO rhtv_workflows(id,payload_json,content_hash,remote_update_time,first_seen_at,changed_at,active) VALUES(?,?,?,?,?,?,1)",
                    (workflow_id, serialized, digest, record.get("updateTime"), timestamp, timestamp))
                created += 1
        if remote_ids:
            placeholders = ",".join("?" for _ in remote_ids)
            deactivated = connection.execute(
                f"UPDATE rhtv_workflows SET active=0 WHERE active=1 AND id NOT IN ({placeholders})", tuple(remote_ids)
            ).rowcount
        else:
            deactivated = 0
        connection.execute(
            "INSERT INTO rhtv_sync_runs(synced_at,remote_total,created_count,updated_count,unchanged_count,deactivated_count) VALUES(?,?,?,?,?,?)",
            (timestamp, remote_total, created, updated, unchanged, deactivated))
        payload = read_cached(connection, keyword)
    return payload


def list_all(keyword: str = "") -> dict:
    return sync_catalog(keyword)


def list_cached(keyword: str = "") -> dict:
    with open_database() as connection:
        count = connection.execute("SELECT COUNT(*) FROM rhtv_workflows WHERE active=1").fetchone()[0]
        if count:
            return read_cached(connection, keyword)
    return sync_catalog(keyword)


def get_detail(workflow_id: str) -> dict:
    normalized = str(workflow_id or "").strip()
    if not normalized.isdigit():
        raise CatalogError("RHTV workflow ID must be numeric")
    with open_database() as connection:
        row = connection.execute("SELECT payload_json FROM rhtv_workflows WHERE id=? AND active=1", (normalized,)).fetchone()
        if row:
            return json.loads(row["payload_json"])
    return normalize_record(post_json(DETAIL_PATH, {"id": normalized}), include_nodes=True)


def main() -> int:
    parser = argparse.ArgumentParser(description="Browse the cached public RHTV workflow catalog")
    mode = parser.add_mutually_exclusive_group(required=True)
    mode.add_argument("--list", action="store_true", help="Read local cache; bootstrap it when empty")
    mode.add_argument("--sync", action="store_true", help="Refresh the public catalog incrementally")
    mode.add_argument("--info", metavar="RHTV_WORKFLOW_ID", help="Show cached workflow details")
    parser.add_argument("--search", default="", help="Filter the local catalog")
    args = parser.parse_args()
    try:
        payload = sync_catalog(args.search) if args.sync else list_cached(args.search) if args.list else get_detail(args.info)
        print(json.dumps(payload, ensure_ascii=False, indent=2))
        return 0
    except (CatalogError, sqlite3.Error) as exc:
        print(json.dumps({"error": "RHTV_CATALOG_ERROR", "message": str(exc)}, ensure_ascii=False), file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
