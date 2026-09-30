#!/usr/bin/env python3
"""Resumable bulk refresh of official AI App API call examples.

The public app list does not reliably include ``invokeExample``.  This job
checks the official ``apiCallDemo`` endpoint for every cached webapp and saves
the returned (redacted) curl example in the local SQLite catalog.
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
import runninghub_app  # noqa: E402
import ai_app_catalog  # noqa: E402


def check_one(api_key: str, webapp_id: str, retries: int) -> tuple[str, bool, str, str]:
    last_error = ""
    for attempt in range(retries + 1):
        try:
            example, error = runninghub_app.get_api_example(api_key, webapp_id)
            return webapp_id, bool(example), example, error
        except BaseException as exc:  # get_app_info reports API errors via SystemExit
            last_error = str(exc) or "API detail request failed"
            if attempt < retries:
                time.sleep(1.0 * (attempt + 1))
    return webapp_id, False, "", last_error[-600:]


def main() -> int:
    parser = argparse.ArgumentParser(description="Refresh official API metadata for cached AI apps")
    parser.add_argument("--db", default=str(ai_app_catalog.DB_PATH))
    parser.add_argument("--workers", type=int, default=10)
    parser.add_argument("--retries", type=int, default=1)
    parser.add_argument("--force", action="store_true", help="recheck even already-checked apps")
    parser.add_argument("--progress", default="/tmp/runninghub_api_sync.json")
    args = parser.parse_args()
    workers = max(1, min(args.workers, 12))
    api_key = runninghub_app.resolve_api_key(None)
    if not api_key:
        raise SystemExit("RunningHub API key is not configured")

    db = ai_app_catalog.connect(Path(args.db))
    db.execute("PRAGMA busy_timeout=30000")
    try:
        where = "1=1" if args.force else "COALESCE(api_checked_at, '') = ''"
        ids = [row[0] for row in db.execute(f"SELECT webapp_id FROM apps WHERE active=1 AND {where} ORDER BY webapp_id")]
        total = len(ids)
        done = 0
        ok = 0
        failed = 0
        started = time.time()

        def write_progress(status: str = "running") -> None:
            payload = {"status": status, "total": total, "completed": done,
                       "apiEnabled": ok, "failed": failed, "remaining": total - done,
                       "workers": workers, "updatedAt": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
                       "elapsedSeconds": round(time.time() - started, 1)}
            Path(args.progress).write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")

        write_progress()
        with concurrent.futures.ThreadPoolExecutor(max_workers=workers) as pool:
            futures = [pool.submit(check_one, api_key, webapp_id, args.retries) for webapp_id in ids]
            for future in concurrent.futures.as_completed(futures):
                webapp_id, enabled, example, error = future.result()
                now = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
                db.execute("UPDATE apps SET api_enabled=?, api_example=?, api_checked_at=?, detail_error=CASE WHEN ?<>'' THEN ? ELSE detail_error END WHERE webapp_id=?",
                           (int(enabled), example, now, error, error, webapp_id))
                done += 1
                ok += int(enabled)
                failed += int(bool(error))
                if done % 25 == 0 or done == total:
                    db.commit()
                    write_progress()
                    print(f"API detail checked {done}/{total}; enabled={ok}; failed={failed}", flush=True)
        db.commit()
        write_progress("completed")
        print(json.dumps({"status": "completed", "total": total, "completed": done,
                          "apiEnabled": ok, "failed": failed}, ensure_ascii=False))
    finally:
        db.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
