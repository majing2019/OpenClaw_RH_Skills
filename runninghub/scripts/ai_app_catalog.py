#!/usr/bin/env python3
"""Incremental local cache for the public RunningHub AI Application directory.

The public list is very large, so synchronization is page-scoped: each page
the browser requests is fetched once, then subsequent refreshes compare hashes
and write only new or changed records. Network access is delegated to the
existing runninghub_app.py helper.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import re
import sqlite3
import subprocess
import sys
import time
import shutil
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
    ):
        if name not in columns:
            db.execute(f"ALTER TABLE apps ADD COLUMN {name} {definition}")
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
    raw = json.dumps({k: app.get(k, "") for k in ("webappId", "title", "description", "coverUrl")}, ensure_ascii=False, sort_keys=True)
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


def fetch_detail(webapp_id: str) -> tuple[list[dict], str]:
    result = subprocess.run(
        [sys.executable, str(APP_SCRIPT), "--info", webapp_id],
        capture_output=True, text=True, timeout=90,
    )
    if result.returncode:
        message = result.stderr.strip() or result.stdout.strip() or "公开节点读取失败"
        return [], message[-600:]
    try:
        payload = json.loads(result.stdout)
    except json.JSONDecodeError:
        return [], "公开节点返回了无效 JSON"
    return payload.get("nodes", []), ""


def infer_purpose(app: dict, nodes: list[dict]) -> str:
    description = str(app.get("description") or "").strip()
    generic = {"", "这是一个可通过 RunningHub API 调用的 AI 应用，具体效果请打开详情查看。"}
    if description not in generic:
        return description
    title = str(app.get("title") or "AI 应用").strip()
    types = {str(node.get("fieldType") or "").upper() for node in nodes}
    title_lower = title.lower()
    if "VIDEO" in types or any(x in title_lower for x in ("video", "视频", "动画", "短片")):
        output = "视频"
    elif "AUDIO" in types or any(x in title_lower for x in ("audio", "音乐", "语音", "配音")):
        output = "音频"
    elif "3D" in title_lower or "3d" in title_lower:
        output = "3D 内容"
    else:
        output = "图片"

    media = [str(node.get("fieldType") or "").upper() for node in nodes]
    input_labels = []
    for kind, label in (("IMAGE", "图片"), ("VIDEO", "视频"), ("AUDIO", "音频")):
        count = media.count(kind)
        if count:
            input_labels.append(f"{count} 个{label}")
    text_count = sum(1 for node in nodes if str(node.get("fieldType") or "").upper() in {"STRING", "LIST", "INT", "FLOAT", "BOOLEAN", "SWITCH"})
    if text_count:
        input_labels.append(f"{text_count} 个文字或控制参数")
    input_hint = "、".join(input_labels) if input_labels else "公开参数"

    prompt = next((str(node.get("fieldValue") or "").strip() for node in nodes
                   if str(node.get("fieldName") or "").lower() in {"prompt", "text", "input_text"} and node.get("fieldValue")), "")
    prompt = " ".join(prompt.split())
    prompt_hint = f"典型效果：{prompt[:150]}{'…' if len(prompt) > 150 else ''}" if prompt else "效果由输入素材和参数决定"
    if output == "视频" and "IMAGE" in media and "VIDEO" in media:
        action = "根据图片、参考视频和文字描述生成视频"
    elif output == "视频" and "IMAGE" in media:
        action = "根据参考图片和文字描述生成视频"
    elif output == "视频" and "VIDEO" in media:
        action = "根据参考视频和文字参数生成视频"
    else:
        action = {"视频": "生成或编辑视频", "音频": "生成或处理音频", "3D 内容": "生成 3D 内容", "图片": "生成或编辑图片"}[output]
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
    created = updated = unchanged = 0
    # A remote page can be reordered between refreshes. Remove the previous
    # positional snapshot for this page so stale cover paths/apps do not leak
    # into the newly refreshed directory.
    db.execute("DELETE FROM app_pages WHERE sort_name=? AND page=?", (sort, page))
    for position, app in enumerate(data.get("apps", [])):
        webapp_id = str(app.get("webappId", "")).strip()
        if not webapp_id:
            continue
        current = db.execute("SELECT content_hash FROM apps WHERE webapp_id = ?", (webapp_id,)).fetchone()
        content_hash = digest(app)
        needs_detail = current is None or current["content_hash"] != content_hash
        if needs_detail:
            nodes, detail_error = fetch_detail(webapp_id)
        else:
            saved = db.execute("SELECT node_json, detail_error FROM apps WHERE webapp_id = ?", (webapp_id,)).fetchone()
            try:
                nodes = json.loads(saved["node_json"] or "[]") if saved else []
            except json.JSONDecodeError:
                nodes = []
            detail_error = saved["detail_error"] if saved else ""
        purpose = infer_purpose(app, nodes)
        cover_file = stable_cover(app)
        values = (webapp_id, app.get("title", ""), app.get("description", ""), cover_file, app.get("coverUrl", ""), purpose, json.dumps(nodes, ensure_ascii=False), detail_error, content_hash, now)
        if current is None:
            db.execute("INSERT INTO apps(webapp_id,title,description,cover_file,cover_url,purpose,node_json,detail_error,content_hash,first_seen,last_seen,active) VALUES(?,?,?,?,?,?,?,?,?,?,?,1)", values[:9] + (now, now))
            created += 1
        elif current["content_hash"] != content_hash:
            db.execute("UPDATE apps SET title=?,description=?,cover_file=?,cover_url=?,purpose=?,node_json=?,detail_error=?,content_hash=?,last_seen=?,active=1 WHERE webapp_id=?", (app.get("title", ""), app.get("description", ""), cover_file, app.get("coverUrl", ""), purpose, json.dumps(nodes, ensure_ascii=False), detail_error, content_hash, now, webapp_id))
            updated += 1
        else:
            db.execute("UPDATE apps SET cover_file=COALESCE(NULLIF(?, ''), cover_file), cover_url=COALESCE(NULLIF(?, ''), cover_url), purpose=COALESCE(NULLIF(?, ''), purpose), last_seen=?,active=1 WHERE webapp_id=?", (cover_file, app.get("coverUrl", ""), purpose, now, webapp_id))
            unchanged += 1
        db.execute("INSERT INTO app_pages(sort_name,webapp_id,page,position,last_seen) VALUES(?,?,?,?,?) ON CONFLICT(sort_name,webapp_id) DO UPDATE SET page=excluded.page,position=excluded.position,last_seen=excluded.last_seen", (sort, webapp_id, page, position, now))
    db.execute("INSERT INTO sync_runs(sort_name,page,size,remote_total,remote_pages,created,updated,unchanged,synced_at) VALUES(?,?,?,?,?,?,?,?,?)", (sort, page, size, int(data.get("total", 0)), int(data.get("pages", 0)), created, updated, unchanged, now))
    db.commit()
    data["sync"] = {"created": created, "updated": updated, "unchanged": unchanged, "page": page, "sort": sort}
    return data


def app_output_type(title: str, description: str, purpose: str, nodes: list[dict]) -> str:
    field_types = {str(node.get("fieldType") or "").upper() for node in nodes}
    haystack = " ".join([title or "", description or "", purpose or ""] + [str(node.get("description") or node.get("fieldName") or "") for node in nodes]).lower()
    if "VIDEO" in field_types or re.search(r"视频|video|动效|短片|动画", haystack):
        return "video"
    if "AUDIO" in field_types or re.search(r"音频|audio|语音|配音|音乐", haystack):
        return "audio"
    if re.search(r"3d|三维|模型", haystack):
        return "3d"
    if re.search(r"文本|文案|文字|text|caption", haystack):
        return "text"
    return "image"


def list_page(db: sqlite3.Connection, sort: str, size: int, page: int, days: int, output_type: str = "") -> dict:
    if output_type:
        candidates = db.execute("""
          SELECT webapp_id, title, description, cover_file, cover_url, purpose, node_json, detail_error
          FROM apps WHERE active=1 ORDER BY last_seen DESC
        """).fetchall()
        matched = []
        for row in candidates:
            try:
                nodes = json.loads(row["node_json"] or "[]")
            except json.JSONDecodeError:
                nodes = []
            if app_output_type(row["title"], row["description"], row["purpose"], nodes) == output_type:
                matched.append(row)
        total = len(matched)
        pages = max(1, (total + size - 1) // size)
        rows = matched[(page - 1) * size: page * size]
        return {"sort": sort, "page": page, "size": size, "type": output_type, "total": total, "pages": pages, "hasNext": page < pages, "apps": [{"title": r["title"], "description": r["description"], "purpose": r["purpose"], "nodes": json.loads(r["node_json"] or "[]"), "detailError": r["detail_error"], "webappId": r["webapp_id"], "coverFile": r["cover_file"], "coverUrl": r["cover_url"]} for r in rows]}

    rows = db.execute("""
      SELECT a.webapp_id, a.title, a.description, a.cover_file, a.cover_url, a.purpose, a.node_json, a.detail_error, p.position
      FROM app_pages p JOIN apps a ON a.webapp_id = p.webapp_id
      WHERE p.sort_name = ? AND p.page = ? ORDER BY p.position LIMIT ? OFFSET ?
    """, (sort, page, size, 0)).fetchall()
    latest = db.execute("SELECT remote_total,remote_pages FROM sync_runs WHERE sort_name=? ORDER BY id DESC LIMIT 1", (sort,)).fetchone()
    total = int(latest["remote_total"] if latest else len(rows))
    pages = int(latest["remote_pages"] if latest else 1)
    return {"sort": sort, "page": page, "size": size, "total": total, "pages": pages, "hasNext": bool(pages and page < pages), "apps": [{"title": r["title"], "description": r["description"], "purpose": r["purpose"], "nodes": json.loads(r["node_json"] or "[]"), "detailError": r["detail_error"], "webappId": r["webapp_id"], "coverFile": r["cover_file"], "coverUrl": r["cover_url"]} for r in rows]}


def get_app(db: sqlite3.Connection, webapp_id: str) -> dict:
    row = db.execute("SELECT webapp_id,title,description,purpose,cover_file,node_json,test_inputs,detail_error FROM apps WHERE webapp_id=?", (webapp_id,)).fetchone()
    if not row:
        return {"webappId": webapp_id, "nodeCount": 0, "nodes": [], "detailError": "该应用尚未进入本地目录缓存"}
    try:
        nodes = json.loads(row["node_json"] or "[]")
    except json.JSONDecodeError:
        nodes = []
    try:
        test_inputs = json.loads(row["test_inputs"] or "[]")
    except json.JSONDecodeError:
        test_inputs = []
    return {"webappId": row["webapp_id"], "title": row["title"], "description": row["description"], "purpose": row["purpose"], "coverFile": row["cover_file"], "nodeCount": len(nodes), "nodes": nodes, "testInputs": test_inputs, "detailError": row["detail_error"]}


def main() -> int:
    parser = argparse.ArgumentParser(description="Incremental local AI application catalog")
    parser.add_argument("--db", default=str(DB_PATH))
    parser.add_argument("--sort", default="RECOMMEND", choices=("RECOMMEND", "HOTTEST", "NEWEST"))
    parser.add_argument("--size", type=int, default=12)
    parser.add_argument("--page", type=int, default=1)
    parser.add_argument("--days", type=int, default=7)
    parser.add_argument("--type", choices=("image", "video", "audio", "text", "3d"), default="")
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
            output = list_page(db, args.sort, max(1, min(args.size, 50)), max(1, args.page), args.days, args.type)
        print(json.dumps(output, ensure_ascii=False, indent=2))
    finally:
        db.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
