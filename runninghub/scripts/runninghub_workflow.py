#!/usr/bin/env python3
"""Official RunningHub ComfyUI Workflow API client.

Uses RUNNINGHUB_API_KEY with the documented workflow endpoints. This replaces
the experimental RHTV browser-session integration; an RHTV canvasId is not a
workflowId and cannot be passed to this client directly.
"""

from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
import tempfile
import time
from pathlib import Path
from urllib.parse import urlparse


SCRIPT_DIR = Path(__file__).resolve().parent
sys.path.insert(0, str(SCRIPT_DIR))
from runninghub import fix_mov_to_mp4, require_api_key  # noqa: E402


DEFAULT_API_BASE = "https://www.runninghub.cn"
WORKFLOW_INFO_PATH = "/api/openapi/getJsonApiFormat"
WORKFLOW_RUN_PATH = "/task/openapi/create"
WORKFLOW_OUTPUTS_PATH = "/task/openapi/outputs"
WORKFLOW_UPLOAD_PATH = "/task/openapi/upload"
PENDING_CODES = {804, 813}
FAILED_CODES = {805}


class WorkflowError(RuntimeError):
    def __init__(self, code: str, message: str, detail=None):
        super().__init__(message)
        self.code = code
        self.message = message
        self.detail = detail


def api_base() -> str:
    return os.environ.get("RUNNINGHUB_WORKFLOW_API_BASE_URL", DEFAULT_API_BASE).rstrip("/")


def api_host() -> str:
    return urlparse(api_base()).netloc


def request_json(path: str, api_key: str, payload: dict, timeout: int = 60) -> dict:
    with tempfile.NamedTemporaryFile(mode="w", suffix=".json", delete=False, encoding="utf-8") as handle:
        json.dump(payload, handle, ensure_ascii=False)
        body_path = handle.name
    try:
        command = [
            "curl", "-sS", "--fail-with-body", "-X", "POST",
            f"{api_base()}{path}", "--max-time", str(timeout),
            "-H", "Content-Type: application/json",
            "-H", f"Host: {api_host()}",
            "-H", f"Authorization: Bearer {api_key}",
            "-d", f"@{body_path}",
        ]
        result = subprocess.run(command, capture_output=True, text=True)
    finally:
        os.unlink(body_path)

    raw = result.stdout or result.stderr
    try:
        response = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise WorkflowError("INVALID_RESPONSE", "RunningHub returned invalid JSON", raw[:500]) from exc
    if result.returncode != 0:
        raise WorkflowError("HTTP_ERROR", str(response.get("msg") or "RunningHub request failed"), response)
    if not isinstance(response, dict):
        raise WorkflowError("INVALID_RESPONSE", "RunningHub returned an unexpected response")
    return response


def ensure_success(response: dict, context: str) -> dict:
    code = int(response.get("code") or 0)
    if code != 0:
        raise WorkflowError("API_ERROR", f"{context}: {response.get('msg') or 'request failed'}", response)
    data = response.get("data")
    return data if isinstance(data, dict) else {"data": data}


def validate_id(value: str, label: str) -> str:
    normalized = str(value or "").strip()
    if not normalized.isdigit():
        raise WorkflowError("INVALID_ID", f"{label} must be a numeric ID")
    return normalized


def get_workflow(api_key: str, workflow_id: str) -> dict:
    workflow_id = validate_id(workflow_id, "workflowId")
    response = request_json(
        WORKFLOW_INFO_PATH,
        api_key,
        {"apiKey": api_key, "workflowId": workflow_id},
    )
    data = ensure_success(response, "Get workflow JSON")
    prompt = data.get("prompt")
    if isinstance(prompt, str):
        try:
            prompt = json.loads(prompt)
        except json.JSONDecodeError as exc:
            raise WorkflowError("INVALID_WORKFLOW", "Workflow prompt is not valid JSON") from exc
    if not isinstance(prompt, dict):
        raise WorkflowError("INVALID_WORKFLOW", "Workflow response does not contain a prompt object")
    return prompt


def value_type(value) -> str:
    if isinstance(value, bool):
        return "BOOLEAN"
    if isinstance(value, int):
        return "INT"
    if isinstance(value, float):
        return "FLOAT"
    if isinstance(value, str):
        return "STRING"
    if isinstance(value, list):
        return "LINK" if len(value) == 2 and isinstance(value[0], str) else "LIST"
    if isinstance(value, dict):
        return "OBJECT"
    return type(value).__name__.upper()


def summarize_workflow(workflow_id: str, prompt: dict) -> dict:
    nodes = []
    for node_id, node in prompt.items():
        if not isinstance(node, dict):
            continue
        inputs = node.get("inputs") if isinstance(node.get("inputs"), dict) else {}
        meta = node.get("_meta") if isinstance(node.get("_meta"), dict) else {}
        fields = [
            {
                "fieldName": name,
                "fieldValue": value,
                "fieldType": value_type(value),
                "modifiable": value_type(value) != "LINK",
            }
            for name, value in inputs.items()
        ]
        nodes.append({
            "nodeId": str(node_id),
            "nodeName": node.get("class_type") or "",
            "title": meta.get("title") or node.get("class_type") or "",
            "fields": fields,
        })
    return {"workflowId": str(workflow_id), "nodeCount": len(nodes), "nodes": nodes}


def parse_assignment(value: str) -> tuple[str, str, str]:
    if ":" not in value or "=" not in value:
        raise WorkflowError("INVALID_NODE", f"Expected nodeId:fieldName=value, got: {value}")
    node_id, remainder = value.split(":", 1)
    field_name, field_value = remainder.split("=", 1)
    if not node_id or not field_name:
        raise WorkflowError("INVALID_NODE", f"Expected nodeId:fieldName=value, got: {value}")
    return node_id, field_name, field_value


def parse_value(value: str):
    try:
        return json.loads(value)
    except json.JSONDecodeError:
        return value


def upload_file(api_key: str, file_path: str) -> str:
    path = Path(file_path)
    if not path.is_file():
        raise WorkflowError("FILE_NOT_FOUND", f"File not found: {file_path}")
    command = [
        "curl", "-sS", "--fail-with-body", "-X", "POST",
        f"{api_base()}{WORKFLOW_UPLOAD_PATH}", "--max-time", "120",
        "-H", f"Host: {api_host()}",
        "-H", f"Authorization: Bearer {api_key}",
        "-F", f"apiKey={api_key}",
        "-F", "fileType=input",
        "-F", f"file=@{path}",
    ]
    result = subprocess.run(command, capture_output=True, text=True)
    raw = result.stdout or result.stderr
    try:
        response = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise WorkflowError("UPLOAD_FAILED", "Upload returned invalid JSON", raw[:500]) from exc
    if result.returncode != 0 or int(response.get("code") or 0) != 0:
        raise WorkflowError("UPLOAD_FAILED", str(response.get("msg") or "File upload failed"), response)
    file_name = (response.get("data") or {}).get("fileName")
    if not file_name:
        raise WorkflowError("UPLOAD_FAILED", "Upload succeeded without a fileName", response)
    return str(file_name)


def build_node_info(api_key: str, node_args: list[str] | None, file_args: list[str] | None) -> list[dict]:
    node_info = []
    for item in node_args or []:
        node_id, field_name, raw_value = parse_assignment(item)
        node_info.append({"nodeId": node_id, "fieldName": field_name, "fieldValue": parse_value(raw_value)})
    for item in file_args or []:
        node_id, field_name, file_path = parse_assignment(item)
        node_info.append({"nodeId": node_id, "fieldName": field_name, "fieldValue": upload_file(api_key, file_path)})
    return node_info


def submit_workflow(api_key: str, workflow_id: str, node_info: list[dict], args) -> str:
    payload: dict = {
        "apiKey": api_key,
        "workflowId": validate_id(workflow_id, "workflowId"),
        "nodeInfoList": node_info,
    }
    if args.instance_type != "default":
        payload["instanceType"] = args.instance_type
    if args.access_password:
        payload["accessPassword"] = args.access_password
    if args.retain_seconds is not None:
        if not 10 <= args.retain_seconds <= 180:
            raise WorkflowError("INVALID_RETAIN_SECONDS", "retainSeconds must be between 10 and 180")
        payload["retainSeconds"] = args.retain_seconds
    response = request_json(WORKFLOW_RUN_PATH, api_key, payload)
    data = ensure_success(response, "Submit workflow")
    task_id = data.get("taskId")
    if not task_id:
        raise WorkflowError("SUBMIT_FAILED", "RunningHub did not return a taskId", response)
    prompt_tips = data.get("promptTips")
    if isinstance(prompt_tips, str):
        try:
            node_errors = (json.loads(prompt_tips) or {}).get("node_errors")
        except json.JSONDecodeError:
            node_errors = None
        if node_errors:
            raise WorkflowError("NODE_ERRORS", "Workflow has invalid node parameters", node_errors)
    return str(task_id)


def wait_for_outputs(api_key: str, task_id: str, timeout: int, poll_interval: int) -> list[dict]:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        response = request_json(
            WORKFLOW_OUTPUTS_PATH,
            api_key,
            {"apiKey": api_key, "taskId": task_id},
        )
        code = int(response.get("code") or 0)
        data = response.get("data")
        if code == 0 and isinstance(data, list) and data:
            return data
        if code in FAILED_CODES:
            raise WorkflowError("TASK_FAILED", str(response.get("msg") or "Workflow task failed"), data)
        if code not in PENDING_CODES and not (code == 0 and not data):
            raise WorkflowError("TASK_ERROR", str(response.get("msg") or "Unexpected task response"), response)
        time.sleep(poll_interval)
    raise WorkflowError("TASK_TIMEOUT", f"Workflow task did not finish within {timeout} seconds")


def guess_extension(item: dict) -> str:
    file_type = str(item.get("fileType") or "").lower().lstrip(".")
    if file_type:
        return file_type
    path = str(item.get("fileUrl") or "").split("?", 1)[0]
    suffix = Path(path).suffix.lower().lstrip(".")
    return suffix or "bin"


def download_outputs(outputs: list[dict], output_path: str | None) -> list[str]:
    downloaded = []
    for index, item in enumerate(outputs, 1):
        url = item.get("fileUrl")
        if not url:
            continue
        extension = guess_extension(item)
        if output_path and len(outputs) == 1:
            destination = Path(output_path)
        elif output_path:
            base = Path(output_path)
            destination = base.with_name(f"{base.stem}_{index}.{extension}")
        else:
            destination = Path(f"/tmp/openclaw/rh-output/workflow_result_{index}.{extension}")
        if destination.suffix.lower().lstrip(".") != extension:
            destination = destination.with_suffix(f".{extension}")
        destination.parent.mkdir(parents=True, exist_ok=True)
        result = subprocess.run(
            ["curl", "-sS", "-L", "--fail-with-body", "--max-time", "300", "-o", str(destination), str(url)],
            capture_output=True,
            text=True,
        )
        if result.returncode != 0:
            raise WorkflowError("DOWNLOAD_FAILED", result.stderr or f"Failed to download output {index}")
        resolved = str(destination.resolve())
        fix_mov_to_mp4(resolved)
        downloaded.append(resolved)
    return downloaded


def run_command(args) -> None:
    api_key = require_api_key(args.api_key)
    node_info = build_node_info(api_key, args.node, args.file)
    task_id = submit_workflow(api_key, args.run, node_info, args)
    print(f"TASK_ID:{task_id}")
    outputs = wait_for_outputs(api_key, task_id, args.timeout, args.poll_interval)
    for path in download_outputs(outputs, args.output):
        print(f"OUTPUT_FILE:{path}")
    costs = [item.get("consumeMoney") or item.get("thirdPartyConsumeMoney") for item in outputs]
    costs = [value for value in costs if value not in (None, "")]
    if costs:
        print(f"COST:¥{costs[0]}")


def main() -> int:
    parser = argparse.ArgumentParser(description="RunningHub official ComfyUI Workflow API client")
    mode = parser.add_mutually_exclusive_group(required=True)
    mode.add_argument("--info", metavar="WORKFLOW_ID", help="Inspect exported workflow JSON")
    mode.add_argument("--run", metavar="WORKFLOW_ID", help="Run a workflow")
    parser.add_argument("--node", action="append", help="Set nodeId:fieldName=value (repeatable)")
    parser.add_argument("--file", action="append", help="Upload and set nodeId:fieldName=/path (repeatable)")
    parser.add_argument("--instance-type", choices=["default", "plus", "ultra"], default="default")
    parser.add_argument("--access-password", help="Password for an encrypted workflow")
    parser.add_argument("--retain-seconds", type=int, help="Keep a shared instance for 10-180 seconds")
    parser.add_argument("--timeout", type=int, default=1200)
    parser.add_argument("--poll-interval", type=int, default=5)
    parser.add_argument("--output", "-o", help="Output path")
    parser.add_argument("--api-key", "-k", help="API key (prefer RUNNINGHUB_API_KEY)")
    args = parser.parse_args()
    try:
        api_key = require_api_key(args.api_key)
        if args.info:
            prompt = get_workflow(api_key, args.info)
            print(json.dumps(summarize_workflow(args.info, prompt), indent=2, ensure_ascii=False))
        else:
            run_command(args)
        return 0
    except WorkflowError as exc:
        payload = {"error": exc.code, "message": exc.message}
        if exc.detail is not None:
            payload["detail"] = exc.detail
        print(json.dumps(payload, ensure_ascii=False), file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
