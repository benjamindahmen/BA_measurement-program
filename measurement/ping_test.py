from __future__ import annotations

import re
import subprocess
import time
from typing import Any

from .config import PingConfig
from .models import GnssState, utc_now_iso


_PACKET_RE = re.compile(
    r"(?P<tx>\d+)\s+packets transmitted,\s+(?P<rx>\d+)\s+(?:packets )?received,"
    r"\s+(?P<loss>[0-9.]+)%\s+packet loss"
)
_RTT_RE = re.compile(r"(?:rtt|round-trip).*=\s*([0-9.]+)/([0-9.]+)/([0-9.]+)")


def run_ping(config: PingConfig, ref_gnss: GnssState) -> dict[str, Any]:
    start_ns = time.monotonic_ns()
    command = [
        "ping",
        "-c",
        str(config.count),
        "-W",
        str(config.timeout_s),
        config.target,
    ]
    try:
        completed = subprocess.run(
            command,
            capture_output=True,
            text=True,
            timeout=config.timeout_s * max(config.count, 1) + 5,
            check=False,
        )
        raw_output = (completed.stdout or "") + (completed.stderr or "")
        parsed = _parse_ping(raw_output)
        success = completed.returncode == 0 and (parsed.get("received") or 0) > 0
        error_text = None if success else f"ping exited with code {completed.returncode}"
    except Exception as exc:
        raw_output = ""
        parsed = {}
        success = False
        error_text = str(exc)

    return {
        "timestamp_system_utc": utc_now_iso(),
        "timestamp_monotonic_ns": start_ns,
        "ref_gnss_time_utc": ref_gnss.gnss_time_utc,
        "ref_gnss_latitude": ref_gnss.latitude,
        "ref_gnss_longitude": ref_gnss.longitude,
        "target": config.target,
        "count": config.count,
        "success": 1 if success else 0,
        "transmitted": parsed.get("transmitted"),
        "received": parsed.get("received"),
        "packet_loss_percent": parsed.get("packet_loss_percent"),
        "rtt_min_ms": parsed.get("rtt_min_ms"),
        "rtt_avg_ms": parsed.get("rtt_avg_ms"),
        "rtt_max_ms": parsed.get("rtt_max_ms"),
        "raw_output": raw_output,
        "error_text": error_text,
    }


def _parse_ping(output: str) -> dict[str, Any]:
    result: dict[str, Any] = {}
    packet_match = _PACKET_RE.search(output)
    if packet_match:
        result["transmitted"] = int(packet_match.group("tx"))
        result["received"] = int(packet_match.group("rx"))
        result["packet_loss_percent"] = float(packet_match.group("loss"))
    rtt_match = _RTT_RE.search(output)
    if rtt_match:
        result["rtt_min_ms"] = float(rtt_match.group(1))
        result["rtt_avg_ms"] = float(rtt_match.group(2))
        result["rtt_max_ms"] = float(rtt_match.group(3))
    return result
