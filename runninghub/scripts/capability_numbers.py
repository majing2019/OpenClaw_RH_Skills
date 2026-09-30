"""Persistent, human-friendly numbers for standard API capabilities."""

from __future__ import annotations

import json
import os
import tempfile
from pathlib import Path

SCRIPT_DIR = Path(__file__).resolve().parent
NUMBER_PATH = SCRIPT_DIR.parent / "data" / "capability_numbers.json"


def number_capabilities(catalog: dict, path: Path = NUMBER_PATH) -> dict:
    """Attach stable catalog numbers, retaining assignments across refreshes."""
    path.parent.mkdir(parents=True, exist_ok=True)
    try:
        saved = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        saved = {}
    if not isinstance(saved, dict):
        saved = {}
    numbers = {str(key): int(value) for key, value in saved.items() if str(value).isdigit()}
    next_number = max(numbers.values(), default=0) + 1
    for item in catalog.get("endpoints", []):
        endpoint = item.get("endpoint")
        if not endpoint:
            continue
        if endpoint not in numbers:
            numbers[endpoint] = next_number
            next_number += 1
        item["catalogNo"] = numbers[endpoint]
    fd, temp_name = tempfile.mkstemp(prefix=".capability-numbers-", suffix=".tmp", dir=path.parent)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            json.dump(numbers, handle, ensure_ascii=False, indent=2)
            handle.write("\n")
        os.replace(temp_name, path)
    finally:
        if os.path.exists(temp_name):
            os.unlink(temp_name)
    return catalog
