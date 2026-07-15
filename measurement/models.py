from __future__ import annotations

import json
import re
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from typing import Any, Iterable


def utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="milliseconds").replace("+00:00", "Z")


def json_dumps(data: Any) -> str:
    return json.dumps(data, ensure_ascii=False, sort_keys=True, default=str)


def get_nested(data: Any, path: Iterable[str], default: Any = None) -> Any:
    current = data
    for key in path:
        if isinstance(current, dict) and key in current:
            current = current[key]
        else:
            return default
    return current


def first_existing(data: Any, keys: Iterable[str], default: Any = None) -> Any:
    if not isinstance(data, dict):
        return default
    for key in keys:
        if key in data and data[key] not in (None, ""):
            return data[key]
    return default


def deep_first_existing(data: Any, key_candidates: Iterable[str], default: Any = None) -> Any:
    key_set = {key.lower() for key in key_candidates}
    stack = [data]
    while stack:
        current = stack.pop()
        if isinstance(current, dict):
            for key, value in current.items():
                if key.lower() in key_set and value not in (None, "") and not isinstance(value, (dict, list)):
                    return value
                if isinstance(value, (dict, list)):
                    stack.append(value)
        elif isinstance(current, list):
            stack.extend(reversed(current))
    return default


def to_float(value: Any) -> float | None:
    if value in (None, ""):
        return None
    if isinstance(value, str):
        value = value.strip().replace(",", ".")
        number_match = re.search(r"[-+]?\d+(?:\.\d+)?", value)
        if number_match:
            value = number_match.group(0)
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def to_int(value: Any) -> int | None:
    if value in (None, ""):
        return None
    try:
        return int(float(str(value).strip()))
    except (TypeError, ValueError):
        return None


@dataclass
class GnssState:
    gnss_time_utc: str | None = None
    valid: bool = False
    latitude: float | None = None
    longitude: float | None = None
    speed_kmh: float | None = None
    course_deg: float | None = None
    altitude_m: float | None = None
    fix_quality: int | None = None
    satellites_used: int | None = None
    hdop: float | None = None
    raw_rmc: str | None = None
    raw_gga: str | None = None

    def as_dict(self) -> dict[str, Any]:
        return asdict(self)
