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
import sqlite3
import subprocess
import sys
import time
from pathlib import Path

SCRIPT_DIR = Path(__file__).resolve().parent
APP_SCRIPT = SCRIPT_DIR / "runninghub_app.py"
DB_PATH = SCRIPT_DIR.parent / "data" / "ai_apps.sqlite3"


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
    raw = json.dumps({k: app.get(k, "") for k in ("webappId", "title", "description", "coverFile")}, ensure_ascii=False, sort_keys=True)
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


def sync_page(db: sqlite3.Connection, sort: str, size: int, page: int, days: int) -> dict:
    data = helper_list(sort, size, page, days)
    now = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
    created = updated = unchanged = 0
    for position, app in enumerate(data.get("apps", [])):
        webapp_id = str(app.get("webappId", "")).strip()
        if not webapp_id:
            continue
        current = db.execute("SELECT content_hash FROM apps WHERE webapp_id = ?", (webapp_id,)).fetchone()
        content_hash = digest(app)
        values = (webapp_id, app.get("title", ""), app.get("description", ""), app.get("coverFile", ""), content_hash, now)
        if current is None:
            db.execute("INSERT INTO apps(webapp_id,title,description,cover_file,content_hash,first_seen,last_seen,active) VALUES(?,?,?,?,?,?,?,1)", values[:5] + (now, now))
            created += 1
        elif current["content_hash"] != content_hash:
            db.execute("UPDATE apps SET title=?,description=?,cover_file=?,content_hash=?,last_seen=?,active=1 WHERE webapp_id=?", (app.get("title", ""), app.get("description", ""), app.get("coverFile", ""), content_hash, now, webapp_id))
            updated += 1
        else:
            db.execute("UPDATE apps SET last_seen=?,active=1 WHERE webapp_id=?", (now, webapp_id))
            unchanged += 1
        db.execute("INSERT INTO app_pages(sort_name,webapp_id,page,position,last_seen) VALUES(?,?,?,?,?) ON CONFLICT(sort_name,webapp_id) DO UPDATE SET page=excluded.page,position=excluded.position,last_seen=excluded.last_seen", (sort, webapp_id, page, position, now))
    db.execute("INSERT INTO sync_runs(sort_name,page,size,remote_total,remote_pages,created,updated,unchanged,synced_at) VALUES(?,?,?,?,?,?,?,?,?)", (sort, page, size, int(data.get("total", 0)), int(data.get("pages", 0)), created, updated, unchanged, now))
    db.commit()
    data["sync"] = {"created": created, "updated": updated, "unchanged": unchanged, "page": page, "sort": sort}
    return data


def list_page(db: sqlite3.Connection, sort: str, size: int, page: int, days: int) -> dict:
    offset = (page - 1) * size
    rows = db.execute("""
      SELECT a.webapp_id, a.title, a.description, a.cover_file, p.position
      FROM app_pages p JOIN apps a ON a.webapp_id = p.webapp_id
      WHERE p.sort_name = ? ORDER BY p.position LIMIT ? OFFSET ?
    """, (sort, size, offset)).fetchall()
    latest = db.execute("SELECT remote_total,remote_pages FROM sync_runs WHERE sort_name=? ORDER BY id DESC LIMIT 1", (sort,)).fetchone()
    total = int(latest["remote_total"] if latest else len(rows))
    pages = int(latest["remote_pages"] if latest else 1)
    return {"sort": sort, "page": page, "size": size, "total": total, "pages": pages, "hasNext": bool(pages and page < pages), "apps": [{"title": r["title"], "description": r["description"], "webappId": r["webapp_id"], "coverFile": r["cover_file"]} for r in rows]}


def main() -> int:
    parser = argparse.ArgumentParser(description="Incremental local AI application catalog")
    parser.add_argument("--db", default=str(DB_PATH))
    parser.add_argument("--sort", default="RECOMMEND", choices=("RECOMMEND", "HOTTEST", "NEWEST"))
    parser.add_argument("--size", type=int, default=12)
    parser.add_argument("--page", type=int, default=1)
    parser.add_argument("--days", type=int, default=7)
    parser.add_argument("--sync", action="store_true")
    parser.add_argument("--list", action="store_true", help="Read the local cache")
    args = parser.parse_args()
    db = connect(Path(args.db))
    try:
        if args.sync:
            output = sync_page(db, args.sort, max(1, min(args.size, 50)), max(1, args.page), args.days)
        else:
            output = list_page(db, args.sort, max(1, min(args.size, 50)), max(1, args.page), args.days)
        print(json.dumps(output, ensure_ascii=False, indent=2))
    finally:
        db.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
