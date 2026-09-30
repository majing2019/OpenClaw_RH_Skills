#!/usr/bin/env python3
"""Sequentially cache public AI app nodes and test inputs, resumably."""

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


def fetch_one(webapp_id: str) -> tuple[str, list[dict], str, dict]:
    nodes, error, metadata = ai_app_catalog.fetch_detail(webapp_id)
    return webapp_id, nodes, error, metadata


def main() -> int:
    parser = argparse.ArgumentParser(description="Sequential full AI app detail sync")
    parser.add_argument("--db", default=str(ai_app_catalog.DB_PATH))
    parser.add_argument("--retry-errors", action="store_true")
    parser.add_argument("--refresh-all", action="store_true", help="Refresh details for every active app")
    parser.add_argument("--limit", type=int, default=0)
    parser.add_argument("--workers", type=int, default=1, help="Concurrent detail requests")
    args = parser.parse_args()
    db = ai_app_catalog.connect(Path(args.db))
    db.execute("ALTER TABLE apps ADD COLUMN test_inputs TEXT NOT NULL DEFAULT '[]'") if "test_inputs" not in {r[1] for r in db.execute("PRAGMA table_info(apps)")} else None
    db.execute("ALTER TABLE apps ADD COLUMN detail_fetched_at TEXT NOT NULL DEFAULT ''") if "detail_fetched_at" not in {r[1] for r in db.execute("PRAGMA table_info(apps)")} else None
    db.commit()
    where = "1=1" if args.refresh_all else "detail_fetched_at=''" if not args.retry_errors else "(detail_fetched_at='' OR detail_error!='')"
    rows = db.execute(f"SELECT webapp_id,title,description,purpose,detail_error FROM apps WHERE active=1 AND {where} ORDER BY rowid").fetchall()
    if args.limit:
        rows = rows[:args.limit]
    total = len(rows)
    ok = failed = 0
    workers = max(1, min(args.workers, 20))
    row_by_id = {row["webapp_id"]: row for row in rows}

    def save_result(index: int, webapp_id: str, nodes: list[dict], error: str, metadata: dict):
        nonlocal ok, failed
        row = row_by_id[webapp_id]
        now = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
        if error:
            # Record the failure while retaining the app's last complete data
            # snapshot. Leave detail_fetched_at empty/unchanged so retries can
            # pick this app up again.
            db.execute("UPDATE apps SET detail_error=? WHERE webapp_id=?", (error, webapp_id))
            db.commit()
            failed += 1
            if index == 1 or index % 10 == 0:
                print(f"details {index}/{total} ok={ok} failed={failed} last={webapp_id}", flush=True)
            return
        app = {"title": row["title"], "description": row["description"], "purpose": row["purpose"]}
        purpose = ai_app_catalog.infer_purpose(app, nodes, metadata)
        official_description = ai_app_catalog.clean_html(metadata.get("description") or "")
        tags_json = json.dumps(metadata.get("tags") or [], ensure_ascii=False)
        covers_json = json.dumps(metadata.get("covers") or [], ensure_ascii=False)
        api_example = str(metadata.get("apiExample") or "")
        api_enabled = 1 if (metadata.get("apiEnabled") or api_example) else 0
        output_type = ai_app_catalog.app_output_type(row["title"], row["description"], purpose, nodes)
        nodes_json = json.dumps(nodes, ensure_ascii=False)
        inputs_json = json.dumps(ai_app_catalog.test_input_specs(nodes), ensure_ascii=False)
        db.execute("""UPDATE apps SET node_json=?,test_inputs=?,detail_error='',purpose=?,detail_fetched_at=?,last_seen=?,
                      official_description=?,tags_json=?,covers_json=?,api_enabled=?,api_example=?,api_checked_at=?,output_type=?
                      WHERE webapp_id=?""",
                   (nodes_json, inputs_json, purpose, now, now, official_description, tags_json,
                    covers_json, api_enabled, api_example, now, output_type, webapp_id))
        db.commit()
        ok += 1
        if index == 1 or index % 10 == 0:
            print(f"details {index}/{total} ok={ok} failed={failed} last={webapp_id}", flush=True)

    if workers == 1:
        for index, row in enumerate(rows, 1):
            save_result(index, *fetch_one(row["webapp_id"]))
    else:
        with concurrent.futures.ThreadPoolExecutor(max_workers=workers) as pool:
            futures = [pool.submit(fetch_one, row["webapp_id"]) for row in rows]
            for index, future in enumerate(concurrent.futures.as_completed(futures), 1):
                save_result(index, *future.result())
    print(json.dumps({"total": total, "ok": ok, "failed": failed}, ensure_ascii=False))
    db.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
