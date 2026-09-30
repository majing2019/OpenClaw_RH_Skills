#!/usr/bin/env python3
"""Incremental local cache for the public RunningHub AI Application directory.

The public list is very large, so synchronization is page-scoped: each page
the browser requests is fetched once, then subsequent refreshes compare hashes
and write only new or changed records. Network access is delegated to the
existing runninghub_app.py helper.
"""

from __future__ import annotations

import argparse
import concurrent.futures
import hashlib
import json
import re
import sqlite3
import subprocess
import sys
import time
import shutil
from html import unescape
from pathlib import Path

SCRIPT_DIR = Path(__file__).resolve().parent
APP_SCRIPT = SCRIPT_DIR / "runninghub_app.py"
DB_PATH = SCRIPT_DIR.parent / "data" / "ai_apps.sqlite3"
COVER_DIR = SCRIPT_DIR.parent / "data" / "ai_app_covers"


def connect(path: Path) -> sqlite3.Connection:
    path.parent.mkdir(parents=True, exist_ok=True)
    db = sqlite3.connect(path)
    db.row_factory = sqlite3.Row
    db.executescript("""
      CREATE TABLE IF NOT EXISTS apps (
        webapp_id TEXT PRIMARY KEY,
        title TEXT NOT NULL DEFAULT '',
        description TEXT NOT NULL DEFAULT '',
        cover_file TEXT NOT NULL DEFAULT '',
        cover_url TEXT NOT NULL DEFAULT '',
        purpose TEXT NOT NULL DEFAULT '',
        node_json TEXT NOT NULL DEFAULT '[]',
        detail_error TEXT NOT NULL DEFAULT '',
        content_hash TEXT NOT NULL,
        first_seen TEXT NOT NULL,
        last_seen TEXT NOT NULL,
        active INTEGER NOT NULL DEFAULT 1
      );
      CREATE TABLE IF NOT EXISTS app_pages (
        sort_name TEXT NOT NULL,
        webapp_id TEXT NOT NULL,
        page INTEGER NOT NULL,
        position INTEGER NOT NULL,
        last_seen TEXT NOT NULL,
        PRIMARY KEY (sort_name, webapp_id),
        FOREIGN KEY (webapp_id) REFERENCES apps(webapp_id)
      );
      CREATE TABLE IF NOT EXISTS sync_runs (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        sort_name TEXT NOT NULL,
        page INTEGER NOT NULL,
        size INTEGER NOT NULL,
        remote_total INTEGER NOT NULL DEFAULT 0,
        remote_pages INTEGER NOT NULL DEFAULT 0,
        created INTEGER NOT NULL DEFAULT 0,
        updated INTEGER NOT NULL DEFAULT 0,
        unchanged INTEGER NOT NULL DEFAULT 0,
        synced_at TEXT NOT NULL
      );
    """)
    columns = {row[1] for row in db.execute("PRAGMA table_info(apps)")}
    for name, definition in (
        ("cover_url", "TEXT NOT NULL DEFAULT ''"),
        ("purpose", "TEXT NOT NULL DEFAULT ''"),
        ("node_json", "TEXT NOT NULL DEFAULT '[]'"),
        ("detail_error", "TEXT NOT NULL DEFAULT ''"),
        ("test_inputs", "TEXT NOT NULL DEFAULT '[]'"),
        ("detail_fetched_at", "TEXT NOT NULL DEFAULT ''"),
        ("official_description", "TEXT NOT NULL DEFAULT ''"),
        ("tags_json", "TEXT NOT NULL DEFAULT '[]'"),
        ("covers_json", "TEXT NOT NULL DEFAULT '[]'"),
        ("api_enabled", "INTEGER NOT NULL DEFAULT 0"),
        ("api_example", "TEXT NOT NULL DEFAULT ''"),
        ("api_checked_at", "TEXT NOT NULL DEFAULT ''"),
        ("output_type", "TEXT NOT NULL DEFAULT ''"),
        ("catalog_no", "INTEGER"),
    ):
        if name not in columns:
            db.execute(f"ALTER TABLE apps ADD COLUMN {name} {definition}")
    next_number = int(db.execute("SELECT COALESCE(MAX(catalog_no),0)+1 FROM apps").fetchone()[0])
    missing_numbers = db.execute("SELECT webapp_id FROM apps WHERE catalog_no IS NULL ORDER BY rowid").fetchall()
    for row in missing_numbers:
        db.execute("UPDATE apps SET catalog_no=? WHERE webapp_id=?", (next_number, row["webapp_id"]))
        next_number += 1
    db.execute("CREATE UNIQUE INDEX IF NOT EXISTS idx_apps_catalog_no ON apps(catalog_no)")
    db.executescript("""
      CREATE TRIGGER IF NOT EXISTS assign_app_catalog_no
      AFTER INSERT ON apps WHEN NEW.catalog_no IS NULL
      BEGIN
        UPDATE apps SET catalog_no=(
          SELECT COALESCE(MAX(catalog_no),0)+1 FROM apps WHERE webapp_id<>NEW.webapp_id
        ) WHERE webapp_id=NEW.webapp_id;
      END;
    """)
    db.execute("CREATE INDEX IF NOT EXISTS idx_apps_output_type ON apps(output_type)")
    db.execute("CREATE INDEX IF NOT EXISTS idx_apps_api_enabled ON apps(api_enabled)")
    return db


def helper_list(sort: str, size: int, page: int, days: int) -> dict:
    result = subprocess.run(
        [sys.executable, str(APP_SCRIPT), "--list", "--sort", sort,
         "--size", str(size), "--page", str(page), "--days", str(days)],
        capture_output=True, text=True, timeout=120,
    )
    if result.returncode:
        message = result.stderr.strip() or result.stdout.strip() or "AI 应用目录请求失败"
        raise RuntimeError(message[-1200:])
    return json.loads(result.stdout)


def digest(app: dict) -> str:
    raw = json.dumps({k: app.get(k, "") for k in ("webappId", "title", "description", "coverUrl", "apiExample", "apiEnabled")}, ensure_ascii=False, sort_keys=True)
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


def fetch_detail(webapp_id: str) -> tuple[list[dict], str, dict]:
    result = subprocess.run(
        [sys.executable, str(APP_SCRIPT), "--info", webapp_id],
        capture_output=True, text=True, timeout=90,
    )
    if result.returncode:
        message = result.stderr.strip() or result.stdout.strip() or "公开节点读取失败"
        return [], message[-600:], {}
    try:
        payload = json.loads(result.stdout)
    except json.JSONDecodeError:
        return [], "公开节点返回了无效 JSON", {}
    metadata = {
        "description": payload.get("description") or "",
        "descriptionEn": payload.get("descriptionEn") or "",
        "webappName": payload.get("webappName") or "",
        "tags": payload.get("tags") or [],
        "covers": payload.get("covers") or [],
        "apiExample": payload.get("apiExample") or "",
        "apiEnabled": bool(payload.get("apiEnabled") or payload.get("apiExample")),
    }
    nodes = payload.get("nodes", [])
    for node in nodes:
        if not node.get("descriptionCn"):
            node["descriptionCn"] = node_description(node)
    return nodes, "", metadata


def test_input_specs(nodes: list[dict]) -> list[dict]:
    fields = ("nodeId", "nodeName", "fieldName", "fieldType", "fieldValue",
              "fieldData", "description", "descriptionCn", "descriptionEn")
    return [{key: node.get(key) for key in fields if key in node} for node in nodes]


def clean_html(value: str) -> str:
    value = re.sub(r"<[^>]+>", " ", str(value or ""))
    return re.sub(r"\s+", " ", unescape(value)).strip()


def node_description(node: dict) -> str:
    description = str(node.get("description") or "").strip()
    if description and not re.fullmatch(r"(?i)(upload images?|input text|duration \(seconds\)|resolution)", description):
        return description
    mapping = {
        "IMAGE": "上传图片",
        "VIDEO": "上传视频",
        "AUDIO": "上传音频",
        "STRING": "输入文字",
        "LIST": "选择参数",
        "INT": "数值参数",
        "FLOAT": "数值参数",
        "BOOLEAN": "开关参数",
        "SWITCH": "选择参数",
    }
    field_type = str(node.get("fieldType") or "").upper()
    if description.lower().startswith("duration"):
        return "时长（秒）"
    if description.lower() == "resolution":
        return "分辨率"
    return mapping.get(field_type, description or "应用参数")


def infer_purpose(app: dict, nodes: list[dict], metadata: dict | None = None) -> str:
    metadata = metadata or {}
    description = clean_html(metadata.get("description") or app.get("description") or "")
    generic = {"", "这是一个可通过 RunningHub API 调用的 AI 应用，具体效果请打开详情查看。"}
    title = str(app.get("title") or "AI 应用").strip()
    types = {str(node.get("fieldType") or "").upper() for node in nodes}
    title_lower = title.lower()
    tags = [str(tag.get("name") or tag.get("nameEn") or "").strip() for tag in (metadata.get("tags") or []) if isinstance(tag, dict)]
    tag_text = " ".join(tags)
    if re.search(r"图生视频|文生视频|视频|video|动画|短片", f"{title} {tag_text}", re.I):
        output = "视频"
    elif re.search(r"音频|音乐|语音|配音|audio|music|speech", f"{title} {tag_text}", re.I):
        output = "音频"
    elif re.search(r"3d|三维", f"{title} {tag_text}", re.I):
        output = "3D 内容"
    else:
        output = "图片"

    media = [str(node.get("fieldType") or "").upper() for node in nodes]
    input_labels = []
    for kind, label in (("IMAGE", "图片"), ("VIDEO", "视频"), ("AUDIO", "音频")):
        count = media.count(kind)
        if count:
            input_labels.append(f"{count} 个{label}")
    text_count = sum(1 for node in nodes if str(node.get("fieldType") or "").upper() == "STRING")
    control_count = sum(1 for node in nodes if str(node.get("fieldType") or "").upper() in {"LIST", "INT", "FLOAT", "BOOLEAN", "SWITCH"})
    if text_count:
        input_labels.append(f"{text_count} 个文字")
    if control_count:
        input_labels.append(f"{control_count} 个控制参数")
    input_hint = "、".join(input_labels) if input_labels else "公开参数"

    prompt = next((str(node.get("fieldValue") or "").strip() for node in nodes
                   if str(node.get("fieldName") or "").lower() in {"prompt", "text", "input_text"} and node.get("fieldValue")), "")
    if not prompt:
        # Many public AI apps expose their prompt as a generic `value` field.
        # Prefer the longest non-empty text default so the catalog still tells
        # the user what the example is intended to generate.
        candidates = [str(node.get("fieldValue") or "").strip() for node in nodes
                      if str(node.get("fieldType") or "").upper() == "STRING" and node.get("fieldValue")]
        prompt = max(candidates, key=len, default="")
    prompt = " ".join(prompt.split())
    prompt_hint = f"典型效果：{prompt[:150]}{'…' if len(prompt) > 150 else ''}" if prompt else "效果由输入素材和参数决定"
    tag_action = next((tag for tag in tags if re.search(r"图生视频|文生视频|视频编辑|图生图|文生图|换装|动作迁移|角色替换", tag, re.I)), "")
    if tag_action:
        action = f"{tag_action}"
    elif output == "视频" and "IMAGE" in media and "VIDEO" in media:
        action = "根据图片、参考视频和文字描述生成视频"
    elif output == "视频" and "IMAGE" in media:
        action = "根据参考图片和文字描述生成视频"
    elif output == "视频" and "VIDEO" in media:
        action = "根据参考视频和文字参数生成视频"
    else:
        action = {"视频": "生成或编辑视频", "音频": "生成或处理音频", "3D 内容": "生成 3D 内容", "图片": "生成或编辑图片"}[output]
    # The official description is often a changelog or billing notice rather
    # than a user-facing explanation. Use the actual public prompt as the
    # example effect and keep tags as the operation label.
    return f"{action}。需要准备：{input_hint}。{prompt_hint}"


def stable_cover(app: dict) -> str:
    source = Path(str(app.get("coverFile") or ""))
    if not source.is_file():
        return ""
    remote_suffix = Path(str(app.get("coverUrl") or "").split("?", 1)[0]).suffix.lower()
    allowed = {".jpg", ".jpeg", ".png", ".webp", ".gif", ".mp4", ".webm", ".mov"}
    suffix = remote_suffix if remote_suffix in allowed else (source.suffix.lower() if source.suffix.lower() in allowed else ".jpg")
    target = COVER_DIR / f"cover_{str(app.get('webappId'))}{suffix}"
    COVER_DIR.mkdir(parents=True, exist_ok=True)
    try:
        if not target.exists() or source.stat().st_size != target.stat().st_size:
            shutil.copyfile(source, target)
        return str(target.resolve())
    except OSError:
        return ""


def sync_page(db: sqlite3.Connection, sort: str, size: int, page: int, days: int) -> dict:
    data = helper_list(sort, size, page, days)
    now = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
    created = updated = unchanged = failed = 0
    apps = [app for app in data.get("apps", []) if str(app.get("webappId", "")).strip()]
    # Refresh all visible app details together. The page's listing and detail
    # metadata are written as one record so API, descriptions, nodes and sample
    # inputs cannot drift independently.
    with concurrent.futures.ThreadPoolExecutor(max_workers=min(10, max(1, len(apps)))) as pool:
        futures = {pool.submit(fetch_detail, str(app["webappId"]).strip()): str(app["webappId"]).strip() for app in apps}
        details = {webapp_id: future.result() for future, webapp_id in futures.items()}

    # A remote page can be reordered between refreshes. Remove the previous
    # positional snapshot for this page so stale cover paths/apps do not leak
    # into the newly refreshed directory.
    db.execute("DELETE FROM app_pages WHERE sort_name=? AND page=?", (sort, page))
    for position, app in enumerate(apps):
        webapp_id = str(app.get("webappId", "")).strip()
        current = db.execute("SELECT * FROM apps WHERE webapp_id = ?", (webapp_id,)).fetchone()
        content_hash = digest(app)
        nodes, detail_error, metadata = details[webapp_id]
        if detail_error:
            failed += 1
            # Keep the existing app snapshot intact on a transient detail
            # failure. For a new app, save only its listing fields and leave
            # detail/API timestamps empty so a later refresh retries it.
            if current is None:
                cover_file = stable_cover(app)
                output_type = app_output_type(app.get("title", ""), app.get("description", ""), "", [])
                listing_api_example = str(app.get("apiExample") or "")
                listing_api_enabled = 1 if (app.get("apiEnabled") or listing_api_example) else 0
                db.execute("""INSERT INTO apps(webapp_id,title,description,cover_file,cover_url,purpose,node_json,detail_error,content_hash,first_seen,last_seen,active,api_enabled,api_example,output_type)
                              VALUES(?,?,?,?,?,?,?,?,?,?,?,1,?,?,?)""",
                           (webapp_id, app.get("title", ""), app.get("description", ""), cover_file,
                            app.get("coverUrl", ""), "", "[]", detail_error, "", now, now,
                            listing_api_enabled, listing_api_example, output_type))
                created += 1
            db.execute("INSERT INTO app_pages(sort_name,webapp_id,page,position,last_seen) VALUES(?,?,?,?,?) ON CONFLICT(sort_name,webapp_id) DO UPDATE SET page=excluded.page,position=excluded.position,last_seen=excluded.last_seen",
                       (sort, webapp_id, page, position, now))
            continue

        purpose = infer_purpose(app, nodes, metadata)
        cover_file = stable_cover(app)
        official_description = clean_html(metadata.get("description") or "")
        tags_json = json.dumps(metadata.get("tags") or [], ensure_ascii=False)
        covers_json = json.dumps(metadata.get("covers") or [], ensure_ascii=False)
        # The AI detail endpoint is the source of truth for API availability
        # and its call example; avoid carrying forward a stale list-page value.
        api_example = str(metadata.get("apiExample") or "")
        api_enabled = 1 if (metadata.get("apiEnabled") or api_example) else 0
        output_type = app_output_type(app.get("title", ""), app.get("description", ""), purpose, nodes)
        api_checked_at = now
        nodes_json = json.dumps(nodes, ensure_ascii=False)
        test_inputs_json = json.dumps(test_input_specs(nodes), ensure_ascii=False)
        changed = current is None or any((
            current["title"] != app.get("title", ""),
            current["description"] != app.get("description", ""),
            current["cover_file"] != cover_file,
            current["cover_url"] != app.get("coverUrl", ""),
            current["purpose"] != purpose,
            current["node_json"] != nodes_json,
            current["official_description"] != official_description,
            current["tags_json"] != tags_json,
            current["covers_json"] != covers_json,
            current["api_enabled"] != api_enabled,
            current["api_example"] != api_example,
            current["output_type"] != output_type,
            current["test_inputs"] != test_inputs_json,
        ))
        if current is None:
            db.execute("""INSERT INTO apps(webapp_id,title,description,cover_file,cover_url,purpose,node_json,detail_error,content_hash,first_seen,last_seen,active,official_description,tags_json,covers_json,api_enabled,api_example,api_checked_at,output_type,test_inputs,detail_fetched_at)
                          VALUES(?,?,?,?,?,?,?,?,?,?,?,1,?,?,?,?,?,?,?,?,?)""",
                       (webapp_id, app.get("title", ""), app.get("description", ""), cover_file,
                        app.get("coverUrl", ""), purpose, nodes_json, "", content_hash, now, now,
                        official_description, tags_json, covers_json, api_enabled, api_example,
                        api_checked_at, output_type, test_inputs_json, now))
            created += 1
        else:
            db.execute("""UPDATE apps SET title=?,description=?,cover_file=?,cover_url=?,purpose=?,node_json=?,detail_error='',content_hash=?,last_seen=?,active=1,
                          official_description=?,tags_json=?,covers_json=?,api_enabled=?,api_example=?,api_checked_at=?,output_type=?,test_inputs=?,detail_fetched_at=?
                          WHERE webapp_id=?""",
                       (app.get("title", ""), app.get("description", ""), cover_file,
                        app.get("coverUrl", ""), purpose, nodes_json, content_hash, now,
                        official_description, tags_json, covers_json, api_enabled, api_example,
                        api_checked_at, output_type, test_inputs_json, now, webapp_id))
            if changed:
                updated += 1
            else:
                unchanged += 1
        db.execute("INSERT INTO app_pages(sort_name,webapp_id,page,position,last_seen) VALUES(?,?,?,?,?) ON CONFLICT(sort_name,webapp_id) DO UPDATE SET page=excluded.page,position=excluded.position,last_seen=excluded.last_seen", (sort, webapp_id, page, position, now))
    db.execute("INSERT INTO sync_runs(sort_name,page,size,remote_total,remote_pages,created,updated,unchanged,synced_at) VALUES(?,?,?,?,?,?,?,?,?)", (sort, page, size, int(data.get("total", 0)), int(data.get("pages", 0)), created, updated, unchanged, now))
    db.commit()
    data["sync"] = {"created": created, "updated": updated, "unchanged": unchanged, "failed": failed, "page": page, "sort": sort}
    return data


def app_output_type(title: str, description: str, purpose: str, nodes: list[dict]) -> str:
    field_types = {str(node.get("fieldType") or "").upper() for node in nodes}
    # Node types describe inputs, not the application's output. Prefer the
    # official app title/purpose and only use media node types as a fallback.
    haystack = " ".join([title or "", description or ""]).lower()
    if re.search(r"图生视频|文生视频|视频|video|动效|短片|动画", haystack):
        return "video"
    if re.search(r"音频|audio|语音|配音|音乐|music|speech", haystack):
        return "audio"
    if re.search(r"图生3d|文生3d|image-to-3d|text-to-3d|三维模型|3d模型", haystack):
        return "3d"
    if re.search(r"图像理解|图片理解|视频理解|图生文|视频转文字|image-to-text|video-to-text|text-to-text", haystack):
        return "text"
    if re.search(r"图生图|文生图|图像|图片|image|picture|photo|海报|插画|换装", haystack):
        return "image"
    if "VIDEO" in field_types:
        return "video"
    if "AUDIO" in field_types:
        return "audio"
    return "image"


def list_page(db: sqlite3.Connection, sort: str, size: int, page: int, days: int, output_type: str = "", api_status: str = "") -> dict:
    if output_type or api_status:
        where = ["active=1"]
        params: list[object] = []
        if output_type:
            where.append("output_type=?")
            params.append(output_type)
        if api_status == "yes":
            where.append("api_enabled=1 AND api_checked_at<>''")
        elif api_status == "no":
            where.append("api_enabled=0 AND api_checked_at<>''")
        clause = " AND ".join(where)
        total = int(db.execute(f"SELECT COUNT(*) FROM apps WHERE {clause}", params).fetchone()[0])
        pages = max(1, (total + size - 1) // size)
        selected = db.execute(f"SELECT webapp_id, catalog_no, title, description, cover_file, cover_url, purpose, node_json, detail_error, official_description, tags_json, covers_json, api_enabled, api_example, api_checked_at FROM apps WHERE {clause} ORDER BY last_seen DESC LIMIT ? OFFSET ?", params + [size, (page - 1) * size]).fetchall()
        rows = selected
        return {"sort": sort, "page": page, "size": size, "type": output_type, "api": api_status, "total": total, "pages": pages, "hasNext": page < pages, "apps": [row_to_app(r) for r in rows]}

    rows = db.execute("""
      SELECT a.webapp_id, a.catalog_no, a.title, a.description, a.cover_file, a.cover_url, a.purpose, a.node_json, a.detail_error, a.official_description, a.tags_json, a.covers_json, a.api_enabled, a.api_example, a.api_checked_at, p.position
      FROM app_pages p JOIN apps a ON a.webapp_id = p.webapp_id
      WHERE p.sort_name = ? AND p.page = ? ORDER BY p.position LIMIT ? OFFSET ?
    """, (sort, page, size, 0)).fetchall()
    latest = db.execute("SELECT remote_total,remote_pages FROM sync_runs WHERE sort_name=? ORDER BY id DESC LIMIT 1", (sort,)).fetchone()
    total = int(latest["remote_total"] if latest else len(rows))
    pages = int(latest["remote_pages"] if latest else 1)
    return {"sort": sort, "page": page, "size": size, "total": total, "pages": pages, "hasNext": bool(pages and page < pages), "apps": [row_to_app(r) for r in rows]}


def row_to_app(row: sqlite3.Row) -> dict:
    try:
        nodes = json.loads(row["node_json"] or "[]")
    except json.JSONDecodeError:
        nodes = []
    try:
        tags = json.loads(row["tags_json"] or "[]")
    except json.JSONDecodeError:
        tags = []
    try:
        covers = json.loads(row["covers_json"] or "[]")
    except json.JSONDecodeError:
        covers = []
    for node in nodes:
        if not node.get("descriptionCn"):
            node["descriptionCn"] = node_description(node)
    webapp_id = row["webapp_id"]
    return {"title": row["title"], "description": row["description"], "purpose": row["purpose"], "officialDescription": row["official_description"], "tags": tags, "covers": covers, "nodes": nodes, "detailError": row["detail_error"], "webappId": webapp_id, "catalogNo": row["catalog_no"], "coverFile": row["cover_file"], "coverUrl": row["cover_url"], "apiEnabled": bool(row["api_enabled"]), "apiStatusKnown": bool(row["api_checked_at"]), "apiExample": row["api_example"], "apiUrl": f"https://www.runninghub.ai/zh-cn/call-api/api-detail/{webapp_id}?apiType=4"}


def get_app(db: sqlite3.Connection, webapp_id: str) -> dict:
    row = db.execute("SELECT webapp_id,catalog_no,title,description,purpose,cover_file,cover_url,node_json,test_inputs,detail_error,official_description,tags_json,covers_json,api_enabled,api_example,api_checked_at FROM apps WHERE webapp_id=?", (webapp_id,)).fetchone()
    if not row:
        return {"webappId": webapp_id, "nodeCount": 0, "nodes": [], "detailError": "该应用尚未进入本地目录缓存"}
    try:
        nodes = json.loads(row["node_json"] or "[]")
    except json.JSONDecodeError:
        nodes = []
    for node in nodes:
        if not node.get("descriptionCn"):
            node["descriptionCn"] = node_description(node)
    try:
        test_inputs = json.loads(row["test_inputs"] or "[]")
    except json.JSONDecodeError:
        test_inputs = []
    try:
        tags = json.loads(row["tags_json"] or "[]")
    except json.JSONDecodeError:
        tags = []
    try:
        covers = json.loads(row["covers_json"] or "[]")
    except json.JSONDecodeError:
        covers = []
    webapp_id = row["webapp_id"]
    return {"webappId": webapp_id, "catalogNo": row["catalog_no"], "title": row["title"], "description": row["description"], "purpose": row["purpose"], "officialDescription": row["official_description"], "tags": tags, "covers": covers, "coverFile": row["cover_file"], "coverUrl": row["cover_url"], "nodeCount": len(nodes), "nodes": nodes, "testInputs": test_inputs, "detailError": row["detail_error"], "apiEnabled": bool(row["api_enabled"]), "apiStatusKnown": bool(row["api_checked_at"]), "apiExample": row["api_example"], "apiUrl": f"https://www.runninghub.ai/zh-cn/call-api/api-detail/{webapp_id}?apiType=4"}


def main() -> int:
    parser = argparse.ArgumentParser(description="Incremental local AI application catalog")
    parser.add_argument("--db", default=str(DB_PATH))
    parser.add_argument("--sort", default="RECOMMEND", choices=("RECOMMEND", "HOTTEST", "NEWEST"))
    parser.add_argument("--size", type=int, default=12)
    parser.add_argument("--page", type=int, default=1)
    parser.add_argument("--days", type=int, default=7)
    parser.add_argument("--type", choices=("image", "video", "audio", "text", "3d"), default="")
    parser.add_argument("--api", choices=("yes", "no"), default="")
    parser.add_argument("--sync", action="store_true")
    parser.add_argument("--list", action="store_true", help="Read the local cache")
    parser.add_argument("--get", metavar="WEBAPP_ID", help="Read one app and its cached public nodes")
    args = parser.parse_args()
    db = connect(Path(args.db))
    try:
        if args.get:
            output = get_app(db, args.get)
        elif args.sync:
            output = sync_page(db, args.sort, max(1, min(args.size, 50)), max(1, args.page), args.days)
        else:
            output = list_page(db, args.sort, max(1, min(args.size, 50)), max(1, args.page), args.days, args.type, args.api)
        print(json.dumps(output, ensure_ascii=False, indent=2))
    finally:
        db.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
