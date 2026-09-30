#!/usr/bin/env python3
"""Resumable full metadata sync for the public RunningHub AI app directory.

The public directory is large (tens of thousands of apps). This job fetches
list metadata in parallel, writes one deduplicated SQLite record per webapp,
and builds 24-item local page indexes. Node details remain lazy and are filled
by the normal page/detail sync path.
"""

from __future__ import annotations

import argparse
import concurrent.futures
import json
import sqlite3
import sys
import time
from pathlib import Path

SCRIPT_DIR = Path(__file__).resolve().parent
sys.path.insert(0, str(SCRIPT_DIR))

import ai_app_catalog  # noqa: E402
import runninghub_app  # noqa: E402


def fetch_page(api_key: str, sort: str, page: int, size: int, days: int, retries: int) -> tuple[int, dict]:
    last_error = ""
    for attempt in range(retries + 1):
        try:
            return page, runninghub_app.list_apps(api_key, sort, size, page, days)
        except Exception as exc:  # retry transient network/API failures
            last_error = str(exc)
            if attempt < retries:
                time.sleep(1.5 * (attempt + 1))
    raise RuntimeError(f"page {page}: {last_error}")


def upsert_page(db: sqlite3.Connection, data: dict, sort: str, remote_page: int, source_size: int) -> int:
    records = data.get("records", [])
    for index, record in enumerate(records):
        webapp_id = runninghub_app._extract_webapp_id(record.get("invokeExample", ""))
        if not webapp_id:
            continue
        title = record.get("title", "") or ""
        description = record.get("description", "") or ""
        cover_url = record.get("cover", "") or ""
        output_type = ai_app_catalog.app_output_type(title, description, "", [])
        existing = db.execute("SELECT purpose,node_json,detail_error,first_seen FROM apps WHERE webapp_id=?", (webapp_id,)).fetchone()
        now = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
        if existing:
            db.execute("""UPDATE apps SET title=?,description=?,cover_url=?,output_type=?,last_seen=?,active=1
                          WHERE webapp_id=?""", (title, description, cover_url, output_type, now, webapp_id))
        else:
            db.execute("""INSERT INTO apps(webapp_id,title,description,cover_file,cover_url,purpose,node_json,detail_error,content_hash,first_seen,last_seen,active,output_type)
                          VALUES(?,?,?,?,?,?,?,?,?,?,?,1,?)""", (webapp_id, title, description, "", cover_url, "", "[]", "", "", now, now, output_type))

        global_position = (remote_page - 1) * source_size + index
        local_page = global_position // 24 + 1
        local_position = global_position % 24
        db.execute("""INSERT INTO app_pages(sort_name,webapp_id,page,position,last_seen) VALUES(?,?,?,?,?)
                     ON CONFLICT(sort_name,webapp_id) DO UPDATE SET page=excluded.page,position=excluded.position,last_seen=excluded.last_seen""",
                    (sort, webapp_id, local_page, local_position, now))
    return len(records)


def main() -> int:
    parser = argparse.ArgumentParser(description="Full resumable AI app metadata sync")
    parser.add_argument("--sort", default="RECOMMEND", choices=("RECOMMEND", "HOTTEST", "NEWEST"))
    parser.add_argument("--size", type=int, default=50, help="Remote page size, max 50")
    parser.add_argument("--workers", type=int, default=6, help="Concurrent list requests")
    parser.add_argument("--start-page", type=int, default=1)
    parser.add_argument("--end-page", type=int, default=0)
    parser.add_argument("--days", type=int, default=7)
    parser.add_argument("--db", default=str(ai_app_catalog.DB_PATH))
    args = parser.parse_args()
    size = max(1, min(args.size, 50))
    workers = max(1, min(args.workers, 12))
    api_key = runninghub_app.resolve_api_key(None)
    if not api_key:
        raise SystemExit("RunningHub API key is not configured")

    db = ai_app_catalog.connect(Path(args.db))
    try:
        # Existing app page positions from smaller page sizes cannot be mixed
        # with the full snapshot. Records themselves remain reusable.
        if args.start_page == 1:
            db.execute("DELETE FROM app_pages WHERE sort_name=?", (args.sort,))
            db.commit()
        _, first = fetch_page(api_key, args.sort, max(1, args.start_page), size, args.days, 2)
        total = int(first.get("total", 0))
        pages = int(first.get("pages", 0))
        end_page = args.end_page or pages
        end_page = min(end_page, pages)
        pending = [p for p in range(max(1, args.start_page), end_page + 1)]
        results: dict[int, dict] = {max(1, args.start_page): first}
        completed = 0
        with concurrent.futures.ThreadPoolExecutor(max_workers=workers) as pool:
            futures = {pool.submit(fetch_page, api_key, args.sort, p, size, args.days, 2): p for p in pending if p not in results}
            for future in concurrent.futures.as_completed(futures):
                page, data = future.result()
                results[page] = data
                completed += 1
                if completed % 10 == 0:
                    print(f"fetched {completed + 1}/{len(pending)} pages", flush=True)
        for page in sorted(results):
            upsert_page(db, results[page], args.sort, page, size)
            if page % 20 == 0:
                db.commit()
                print(f"stored page {page}/{end_page}", flush=True)
        now = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
        db.execute("INSERT INTO sync_runs(sort_name,page,size,remote_total,remote_pages,created,updated,unchanged,synced_at) VALUES(?,?,?,?,?,?,?,?,?)",
                    (args.sort, 1, 24, total, (total + 23) // 24, 0, 0, total, now))
        db.commit()
        print(json.dumps({"sort": args.sort, "total": total, "remotePages": pages, "storedPages": end_page, "workers": workers}, ensure_ascii=False))
    finally:
        db.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
