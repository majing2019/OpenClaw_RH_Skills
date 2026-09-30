#!/usr/bin/env python3
"""Browse and incrementally cache the public RHTV common-workflow catalog."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
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
RHTV_LIBRARY_URL = (
    "https://rhtv.runninghub.ai/projects/canvas/camp"
    "?section=workflow&type=workflow&category=recommended"
)
DEFAULT_DB_PATH = Path(__file__).resolve().parents[1] / "data" / "rhtv_catalog.sqlite3"
MEDIA_NAMES = {"image": "图片", "video": "视频", "audio": "音频", "string": "文本", "3d": "3D 模型", "unknown": "内容"}
NORMALIZER_VERSION = 5


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


def output_urls(node: dict) -> set[str]:
    data = node.get("data") if isinstance(node.get("data"), dict) else {}
    urls: set[str] = set()
    for output in data.get("output") or []:
        if not isinstance(output, dict):
            continue
        for key in ("url", "originalUrl", "thumbnail"):
            value = output.get(key)
            if isinstance(value, str) and value.startswith(("https://", "http://")):
                urls.add(value)
    return urls


def preview_component(nodes: list[dict], edges: list[dict], thumbnail: str) -> tuple[list[dict], list[dict]]:
    """Return the graph branch that produced the catalog preview."""
    if not thumbnail:
        return nodes, edges
    preview_nodes = [str(node.get("id") or "") for node in nodes if thumbnail in output_urls(node)]
    if not preview_nodes:
        return nodes, edges
    reverse: dict[str, set[str]] = {}
    for edge in edges:
        source, target = str(edge.get("source") or ""), str(edge.get("target") or "")
        if source and target:
            reverse.setdefault(target, set()).add(source)
    # Some canvas tools retain their source as node metadata instead of a
    # visible ReactFlow edge (for example, Depth Capture).
    for node in nodes:
        data = node.get("data") if isinstance(node.get("data"), dict) else {}
        target = str(node.get("id") or "")
        for key in ("sourceNodeId", "inheritedFrom"):
            source = str(data.get(key) or "")
            if source and target:
                reverse.setdefault(target, set()).add(source)
    included = set(preview_nodes)
    pending = list(preview_nodes)
    while pending:
        for source in reverse.get(pending.pop(), set()):
            if source not in included:
                included.add(source)
                pending.append(source)
    selected_nodes = [node for node in nodes if str(node.get("id") or "") in included]
    selected_edges = [edge for edge in edges
                      if str(edge.get("source") or "") in included and str(edge.get("target") or "") in included]
    return selected_nodes or nodes, selected_edges


def clean_prompt(value: object) -> str:
    return str(value or "").strip() if isinstance(value, (str, int, float)) else ""


def node_prompt(node: dict) -> str:
    data = node.get("data") if isinstance(node.get("data"), dict) else {}
    params = data.get("params") if isinstance(data.get("params"), dict) else {}
    # The top-level value is the creator's original prompt. params.prompt is often
    # an automatically translated English copy, so prefer the former. RHTV caps
    # some top-level prompt snapshots at roughly 4,000 characters; when the model
    # parameter preserves a longer copy, use it so the displayed input is complete.
    original = clean_prompt(data.get("prompt"))
    model_prompt = clean_prompt(params.get("prompt"))
    if len(original) >= 3990 and len(model_prompt) > len(original):
        return model_prompt
    return original or model_prompt


def uploaded_urls(node: dict) -> list[str]:
    data = node.get("data") if isinstance(node.get("data"), dict) else {}
    params = data.get("params") if isinstance(data.get("params"), dict) else {}
    values: list[object] = [data.get("sourceObjects"), data.get("toolsSourceUrl")]
    values.extend(params.get(key) for key in
                  ("imageUrls", "videoUrls", "audioUrls", "image", "video", "audio"))
    urls: list[str] = []
    for value in values:
        candidates = value if isinstance(value, list) else [value]
        for candidate in candidates:
            if isinstance(candidate, str) and candidate.startswith(("https://", "http://")) and "/uploads/" in candidate:
                urls.append(candidate)
    return list(dict.fromkeys(urls))


def source_inputs(nodes: list[dict], edges: list[dict]) -> list[dict]:
    results: list[dict] = []
    seen: set[str] = set()
    counters = {"image": 0, "video": 0, "audio": 0, "unknown": 0}
    incoming = {str(edge.get("target") or "") for edge in edges}
    incoming.update(str(node.get("id") or "") for node in nodes
                    if isinstance(node.get("data"), dict)
                    and ((node.get("data") or {}).get("sourceNodeId") or (node.get("data") or {}).get("inheritedFrom")))
    for node in nodes:
        uploads = uploaded_urls(node)
        candidates = [(url, True) for url in uploads]
        node_id = str(node.get("id") or "")
        # Older public canvases sometimes retain a referenced/generated asset as
        # a root output instead of preserving its original upload URL.
        if node_id not in incoming and not uploads:
            candidates.extend((url, False) for url in output_urls(node))
        for url, is_upload in candidates:
            if url in seen:
                continue
            seen.add(url)
            kind = media_type(url)
            if kind == "unknown":
                kind = node_media_type(node)
            counters[kind] = counters.get(kind, 0) + 1
            name = MEDIA_NAMES.get(kind, "素材")
            results.append({
                "id": f"{node.get('id') or 'media'}-{counters[kind]}",
                "type": kind,
                "label": f"{name} {counters[kind]}",
                "description": (f"创作者上传的原始{name}，用于复刻当前预览成片。" if is_upload
                                else f"公开画布保存的参考{name}；原始上传地址未保留。"),
                "url": url,
                "fileName": Path(urlparse(url).path).name,
                "nodeId": str(node.get("id") or ""),
                "origin": "upload" if is_upload else "public-reference",
            })
    return results


def prompt_inputs(nodes: list[dict]) -> list[dict]:
    results: list[dict] = []
    seen: set[str] = set()
    counters: dict[str, int] = {}
    for node in nodes:
        prompt = node_prompt(node)
        normalized = re.sub(r"\s+", " ", prompt).strip()
        if len(normalized) < 4 or normalized in seen:
            continue
        seen.add(normalized)
        kind = node_media_type(node)
        counters[kind] = counters.get(kind, 0) + 1
        target = MEDIA_NAMES.get(kind, "内容")
        results.append({
            "id": f"prompt-{node.get('id') or len(results) + 1}",
            "type": "string",
            "label": f"{target}提示词 {counters[kind]}",
            "description": f"用于生成或处理{target}的原始文字指令。",
            "value": prompt,
            "targetType": kind,
            "nodeId": str(node.get("id") or ""),
            "modelCode": (node.get("data") or {}).get("modelCode"),
        })
    return results


def text_excerpt(text: str, limit: int = 180) -> str:
    compact = re.sub(r"\s+", " ", text).strip()
    compact = re.sub(r"^【[^】]{1,20}】\s*", "", compact)
    compact = compact.lstrip("【 ")
    if len(compact) <= limit:
        return compact
    cut = compact[:limit].rsplit(" ", 1)[0].rstrip("，,。.;；：:")
    return f"{cut}…"


def has_chinese(text: str) -> bool:
    return bool(re.search(r"[\u4e00-\u9fff]", text))


def effect_hint(prompt: str, name: str) -> str:
    """Create a concise Chinese effect summary without pretending to translate every prompt."""
    lower = prompt.casefold()
    title = name.casefold()
    if any(word in title for word in ("map", "マップ", "맵", "地图")) and any(word in lower for word in ("city", "travel", "城市", "旅行")):
        return "人物会在不同城市的 3D 地图场景中切换背景与造型，并用环绕镜头完成连续转场。"
    if any(word in title for word in ("makeup", "bare", "素颜", "妆", "민낯")) and any(word in lower for word in ("dance", "舞蹈", "跳舞")):
        return "人物会在连续舞蹈动作中完成素颜、妆容或造型的无缝变化，保持同一人物和动作连贯。"
    if any(word in title for word in ("transform", "trans", "change", "outfit", "dance", "换装", "变装", "変身", "변신")) and any(word in lower for word in ("dance", "movement", "舞蹈", "动作")):
        return "成片会复刻参考视频中的人物动作和节奏，并在卡点处完成服装或造型的连续切换。"
    if any(word in title for word in ("floating", "悬浮")) and any(word in lower for word in ("product", "产品", "奶茶", "商品")):
        return "成片以产品悬浮和人物互动为核心，呈现带轻奇幻感的产品广告效果。"
    if any(word in title for word in ("pet", "dog", "cat", "宠物", "狗", "猫")) and any(word in lower for word in ("hair", "hairstyle", "发型")):
        return "成片让宠物在固定机位中连续切换不同发型或造型，形成轻松有趣的短视频效果。"
    if has_chinese(prompt):
        return text_excerpt(prompt)
    return "成片内容、人物动作、镜头运动和风格由下方的视频生成提示词精确控制。"


def output_settings(nodes: list[dict]) -> dict:
    video_nodes = [node for node in nodes if node_media_type(node) == "video" and node_prompt(node)]
    node = video_nodes[-1] if video_nodes else (nodes[-1] if nodes else {})
    data = node.get("data") if isinstance(node.get("data"), dict) else {}
    params = data.get("params") if isinstance(data.get("params"), dict) else {}
    settings = {key: params.get(key) for key in ("aspectRatio", "ratio", "duration", "resolution", "audio")
                if params.get(key) not in (None, "", [])}
    try:
        if float(settings.get("duration", 1)) <= 0:
            settings.pop("duration", None)
    except (TypeError, ValueError):
        pass
    return settings


def rich_description(name: str, nodes: list[dict], media_inputs: list[dict], prompts: list[dict], preview_type: str) -> str:
    settings = output_settings(nodes)
    ratio = settings.get("aspectRatio") or settings.get("ratio")
    duration = settings.get("duration")
    shape = "竖屏" if ratio in ("9:16", "3:4") else "横屏" if ratio in ("16:9", "4:3") else ""
    timing = f"约 {duration} 秒" if duration else ""
    format_text = "、".join(part for part in (shape, timing) if part)
    video_prompts = [item["value"] for item in prompts if item.get("targetType") == "video"]
    main_prompt = video_prompts[-1] if video_prompts else (prompts[-1]["value"] if prompts else "")
    nouns = {"video": "视频", "image": "图片", "audio": "音频", "string": "文本结果", "3d": "3D 内容"}
    product = nouns.get(preview_type) or nouns.get(node_media_type(nodes[-1]) if nodes else "") or "内容"
    measure = "一支" if product == "视频" else "一张" if product == "图片" else "一段" if product in ("音频", "文本结果") else "一项"
    intro = f"这个工作流会生成{measure}{format_text + '、' if format_text else ''}以「{name}」为主题的{product}。"
    if product == "视频":
        detail = effect_hint(main_prompt, name) if main_prompt else "公开画布没有保留可读取的视频提示词，请以预览成片和节点结构为准。"
    else:
        detail = text_excerpt(main_prompt) if main_prompt and has_chinese(main_prompt) else f"具体{product}内容由下方原始提示词和参考素材控制。"
    counts: dict[str, int] = {}
    for item in media_inputs:
        counts[item["type"]] = counts.get(item["type"], 0) + 1
    needs = "、".join(f"{count} 个{MEDIA_NAMES.get(kind, kind)}素材" for kind, count in counts.items())
    if needs:
        detail += f" 复刻该预览需要准备{needs}。"
    return intro + detail


def input_summary(media_inputs: list[dict], prompts: list[dict]) -> str:
    counts: dict[str, int] = {}
    for item in media_inputs:
        counts[item["type"]] = counts.get(item["type"], 0) + 1
    media = "、".join(f"{count} 个{MEDIA_NAMES.get(kind, kind)}" for kind, count in counts.items())
    prefix = f"需要：{media}" if media else "未识别到需要额外上传的媒体素材"
    return f"{prefix}；工作流内含 {len(prompts)} 段原始文字指令。"


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


def normalize_record(record: dict, include_nodes: bool = False) -> dict:
    graph = graph_from(record)
    raw_nodes = graph.get("nodes") if isinstance(graph.get("nodes"), list) else []
    all_nodes = [node for node in raw_nodes if isinstance(node, dict) and node.get("type") != "group"]
    all_edges = [edge for edge in (graph.get("edges") or []) if isinstance(edge, dict)]
    thumbnail = str(record.get("thumbnail") or "")
    nodes, edges = preview_component(all_nodes, all_edges, thumbnail)
    models = sorted({str(node.get("data", {}).get("modelCode")) for node in nodes
                     if isinstance(node.get("data"), dict) and node.get("data", {}).get("modelCode")})
    inputs, outputs = graph_io(nodes, edges)
    media_inputs = source_inputs(nodes, edges)
    prompts = prompt_inputs(nodes)
    name = str(record.get("name") or "未命名工作流")
    preview_type = media_type(thumbnail)
    effect_description = rich_description(name, nodes, media_inputs, prompts, preview_type)
    item = {
        "id": str(record.get("id") or ""), "code": str(record.get("code") or ""),
        "name": name,
        "description": str(record.get("description") or ""),
        "chineseDescription": effect_description,
        "effectDescription": effect_description,
        "inputSummary": input_summary(media_inputs, prompts),
        "requiredInputs": [*media_inputs, *prompts],
        "mediaInputs": media_inputs,
        "promptInputs": prompts,
        "outputSettings": output_settings(nodes),
        "thumbnail": thumbnail if thumbnail.startswith(("https://", "http://")) else "",
        "previewType": preview_type, "updateTime": record.get("updateTime"),
        "nodeCount": len(nodes), "edgeCount": len(edges),
        "canvasNodeCount": len(all_nodes), "canvasEdgeCount": len(all_edges),
        "outputTypes": sorted({entry["type"] for entry in outputs if entry["type"] != "unknown"}),
        "models": models, "inputs": inputs, "outputs": outputs,
        "sourceUrl": RHTV_LIBRARY_URL,
    }
    if include_nodes:
        item["nodes"] = [compact_node(node) for node in nodes]
    return item


def record_hash(record: dict) -> str:
    stable = {key: record.get(key) for key in
              ("id", "code", "name", "description", "thumbnail", "updateTime", "workflowContent")}
    stable["_normalizerVersion"] = NORMALIZER_VERSION
    encoded = json.dumps(stable, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def list_page(page: int, size: int) -> dict:
    return post_json(LIST_PATH, {"page": page, "size": min(max(size, 1), MAX_PAGE_SIZE)})


def fetch_all_records() -> tuple[list[dict], int]:
    first = list_page(1, MAX_PAGE_SIZE)
    first_records = first.get("records")
    if not isinstance(first_records, list):
        raise CatalogError("RHTV catalog page 1 did not contain a record list")
    records = list(first_records)
    pages = max(1, int(first.get("pages") or 1))
    remote_total = int(first.get("total") or len(records))
    if pages > 1:
        with ThreadPoolExecutor(max_workers=min(4, pages - 1)) as executor:
            for page, result in zip(range(2, pages + 1), executor.map(lambda page: list_page(page, MAX_PAGE_SIZE), range(2, pages + 1))):
                page_records = result.get("records")
                if not isinstance(page_records, list):
                    raise CatalogError(f"RHTV catalog page {page} did not contain a record list")
                records.extend(page_records)
    if len(records) != remote_total:
        raise CatalogError(
            f"RHTV catalog is incomplete ({len(records)} records received, {remote_total} expected); local snapshot was kept"
        )
    if any(not isinstance(record, dict) or not str(record.get("id") or "") for record in records):
        raise CatalogError("RHTV catalog contains a record without a valid ID; local snapshot was kept")
    record_ids = [str(record["id"]) for record in records]
    if len(set(record_ids)) != len(record_ids):
        raise CatalogError("RHTV catalog contains duplicate IDs; local snapshot was kept")
    return records, remote_total


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
    # Older local databases may contain the retired local detail-link field.
    for item in workflows:
        item.pop("detailPath", None)
    normalized_keyword = keyword.casefold().strip()
    if normalized_keyword:
        workflows = [item for item in workflows if normalized_keyword in " ".join([
            item.get("name", ""), item.get("description", ""), item.get("chineseDescription", ""),
            item.get("effectDescription", ""), item.get("inputSummary", ""),
            *(entry.get("value", "") for entry in (item.get("promptInputs") or [])),
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
            if previous:
                old_row = connection.execute(
                    "SELECT payload_json FROM rhtv_workflows WHERE id=?", (workflow_id,)
                ).fetchone()
                old_payload = json.loads(old_row["payload_json"]) if old_row else {}
                if old_payload.get("canvasNodeCount", 0) and not payload.get("canvasNodeCount", 0):
                    raise CatalogError(
                        f"RHTV workflow {workflow_id} lost its canvas data in the refresh response; local snapshot was kept"
                    )
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
            payload = json.loads(row["payload_json"])
            payload.pop("detailPath", None)
            return payload
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
