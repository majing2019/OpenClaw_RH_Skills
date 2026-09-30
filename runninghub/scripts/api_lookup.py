#!/usr/bin/env python3
"""Look up cached RunningHub API instructions by catalog number."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

import ai_app_catalog
import capability_numbers

SKILL_DIR = Path(__file__).resolve().parent.parent
CAPABILITIES_PATH = SKILL_DIR / "data" / "capabilities.json"
APP_DB_PATH = SKILL_DIR / "data" / "ai_apps.sqlite3"


def standard_api(item: dict) -> dict:
    params = item.get("params", [])
    payload = {}
    media = any(str(param.get("type", "")).upper() in {"IMAGE", "VIDEO", "AUDIO"} for param in params)
    for param in params:
        kind = str(param.get("type", "")).upper()
        has_default = "default" in param
        if not param.get("required") and not has_default:
            continue
        if has_default:
            value = param["default"]
        elif kind in {"IMAGE", "VIDEO", "AUDIO"}:
            suffix = {"IMAGE": "png", "VIDEO": "mp4", "AUDIO": "mp3"}[kind]
            value = "https://example.com/replace-with-" + kind.lower() + "." + suffix
            if param.get("multiple"):
                value = [value]
        elif kind in {"LIST", "SIZE"}:
            value = (param.get("options") or ["REPLACE_WITH_ALLOWED_OPTION"])[0]
        elif kind == "BOOLEAN":
            value = False
        elif kind == "INT":
            value = param.get("min", 1)
        elif kind == "FLOAT":
            value = param.get("min", 1.0)
        elif kind == "STRING":
            key = param.get("key", "")
            value = "Describe the content you want to generate" if any(word in key.lower() for word in ("prompt", "text", "description")) else "REPLACE_WITH_" + key
        else:
            value = "REPLACE_WITH_" + param.get("key", "parameter")
        payload[param["key"]] = value

    endpoint = item["endpoint"]
    lines = []
    if media:
        lines.extend([
            "# 先上传本地媒体，取响应中的 data.download_url 替换请求体示例地址",
            "curl --request POST 'https://www.runninghub.cn/openapi/v2/media/upload/binary' \\",
            '  --header "Authorization: Bearer $RUNNINGHUB_API_KEY" \\',
            "  --form 'file=@./your-media-file'",
            "",
        ])
    lines.extend([
        "curl --request POST 'https://www.runninghub.cn/openapi/v2/" + endpoint + "' \\",
        '  --header "Authorization: Bearer $RUNNINGHUB_API_KEY" \\',
        "  --header 'Content-Type: application/json' \\",
        "  --data-binary @- <<'JSON'",
        json.dumps(payload, ensure_ascii=False, indent=2),
        "JSON",
        "",
        "# 使用提交响应里的 taskId 查询任务；重复查询直到任务完成",
        """curl --request POST 'https://www.runninghub.cn/openapi/v2/query' --header "Authorization: Bearer $RUNNINGHUB_API_KEY" --header 'Content-Type: application/json' --data '{"taskId":"<taskId>"}'""",
    ])
    return {
        "kind": "standard",
        "catalogNo": item["catalogNo"],
        "name": item.get("name_cn") or item.get("name_en") or endpoint,
        "endpoint": endpoint,
        "apiEnabled": True,
        "apiUrl": "https://www.runninghub.cn/call-api/standard-api",
        "apiExample": "\n".join(lines),
        "params": params,
    }


def lookup(number: int, kind: str | None) -> list[dict]:
    results = []
    if kind in (None, "standard"):
        catalog = json.loads(CAPABILITIES_PATH.read_text(encoding="utf-8"))
        capability_numbers.number_capabilities(catalog)
        match = next((item for item in catalog.get("endpoints", []) if item.get("catalogNo") == number), None)
        if match:
            results.append(standard_api(match))
    if kind in (None, "app"):
        db = ai_app_catalog.connect(APP_DB_PATH)
        try:
            row = db.execute(
                "SELECT catalog_no,webapp_id,title,api_enabled,api_example,api_checked_at FROM apps WHERE catalog_no=?",
                (number,),
            ).fetchone()
            if row:
                webapp_id = row["webapp_id"]
                results.append({
                    "kind": "app",
                    "catalogNo": row["catalog_no"],
                    "name": row["title"],
                    "webappId": webapp_id,
                    "apiEnabled": bool(row["api_enabled"]),
                    "apiStatusKnown": bool(row["api_checked_at"]),
                    "apiUrl": "https://www.runninghub.ai/zh-cn/call-api/api-detail/" + webapp_id + "?apiType=4",
                    "apiExample": row["api_example"] or "",
                    "note": "官方 API 请求示例尚未缓存；请打开 apiUrl 查看官方当前参数。" if not row["api_example"] else "",
                })
        finally:
            db.close()
    return results


def main() -> int:
    parser = argparse.ArgumentParser(description="Return RunningHub API instructions by catalog number")
    parser.add_argument("--number", type=int, required=True, help="Catalog number shown in the browser")
    parser.add_argument("--kind", choices=("standard", "app"), help="Disambiguate standard capability or AI app")
    args = parser.parse_args()
    if args.number < 1:
        parser.error("--number must be a positive integer")
    results = lookup(args.number, args.kind)
    if not results:
        print("未找到编号 " + str(args.number) + "。请确认编号或目录类型。", file=sys.stderr)
        return 1
    print(json.dumps(results[0] if args.kind else results, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
