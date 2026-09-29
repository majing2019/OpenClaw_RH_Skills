#!/usr/bin/env python3
"""Sequentially cache public AI app nodes and test inputs, resumably."""

from __future__ import annotations

import argparse
import json
import sqlite3
import sys
import time
from pathlib import Path

SCRIPT_DIR = Path(__file__).resolve().parent
sys.path.insert(0, str(SCRIPT_DIR))
import ai_app_catalog  # noqa: E402


def input_specs(nodes: list[dict]) -> list[dict]:
    """Keep the exact public values needed to reproduce a test invocation."""
    result = []
    for node in nodes:
        result.append({key: node.get(key) for key in (
            "nodeId", "nodeName", "fieldName", "fieldType", "fieldValue",
            "fieldData", "description", "descriptionCn", "descriptionEn",
        ) if key in node})
    return result


def main() -> int:
    parser = argparse.ArgumentParser(description="Sequential full AI app detail sync")
    parser.add_argument("--db", default=str(ai_app_catalog.DB_PATH))
    parser.add_argument("--retry-errors", action="store_true")
    parser.add_argument("--limit", type=int, default=0)
    args = parser.parse_args()
    db = ai_app_catalog.connect(Path(args.db))
    db.execute("ALTER TABLE apps ADD COLUMN test_inputs TEXT NOT NULL DEFAULT '[]'") if "test_inputs" not in {r[1] for r in db.execute("PRAGMA table_info(apps)")} else None
    db.execute("ALTER TABLE apps ADD COLUMN detail_fetched_at TEXT NOT NULL DEFAULT ''") if "detail_fetched_at" not in {r[1] for r in db.execute("PRAGMA table_info(apps)")} else None
    db.commit()
    where = "detail_fetched_at=''" if not args.retry_errors else "(detail_fetched_at='' OR detail_error!='')"
    rows = db.execute(f"SELECT webapp_id,title,description,purpose,detail_error FROM apps WHERE active=1 AND {where} ORDER BY rowid").fetchall()
    if args.limit:
        rows = rows[:args.limit]
    total = len(rows)
    ok = failed = 0
    for index, row in enumerate(rows, 1):
        webapp_id = row["webapp_id"]
        nodes, error = ai_app_catalog.fetch_detail(webapp_id)
        now = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
        app = {"title": row["title"], "description": row["description"], "purpose": row["purpose"]}
        purpose = ai_app_catalog.infer_purpose(app, nodes)
        db.execute("""UPDATE apps SET node_json=?,test_inputs=?,detail_error=?,purpose=?,detail_fetched_at=?,last_seen=? WHERE webapp_id=?""",
                   (json.dumps(nodes, ensure_ascii=False), json.dumps(input_specs(nodes), ensure_ascii=False), error, purpose, now, now, webapp_id))
        db.commit()
        if error:
            failed += 1
        else:
            ok += 1
        if index == 1 or index % 10 == 0:
            print(f"details {index}/{total} ok={ok} failed={failed} last={webapp_id}", flush=True)
    print(json.dumps({"total": total, "ok": ok, "failed": failed}, ensure_ascii=False))
    db.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
