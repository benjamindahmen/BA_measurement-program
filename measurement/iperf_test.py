from __future__ import annotations

import json
import subprocess
import time
from typing import Any

from .config import IperfConfig
from .models import GnssState, utc_now_iso


def run_iperf(config: IperfConfig, direction: str, ref_gnss: GnssState) -> dict[str, Any]:
    bytes_requested = config.bytes_download if direction == "download" else config.bytes_upload
    ports = [config.port] + [port for port in config.fallback_ports if port != config.port]
    last_result: dict[str, Any] | None = None
    for port in ports:
        result = _run_single(config, direction, bytes_requested, port, ref_gnss)
        if result["success"]:
            return result
        last_result = result
    return last_result or _base_result(config, direction, bytes_requested, config.port, ref_gnss)


def _run_single(
    config: IperfConfig,
    direction: str,
    bytes_requested: str,
    port: int,
    ref_gnss: GnssState,
) -> dict[str, Any]:
    start_utc = utc_now_iso()
    start_ns = time.monotonic_ns()
    command = [
        "iperf3",
        "-c",
        config.server,
        "-p",
        str(port),
        "-n",
        bytes_requested,
        "-P",
        str(config.parallel_streams),
        "-J",
    ]
    if direction == "download":
        command.insert(1, "-R")

    result = _base_result(config, direction, bytes_requested, port, ref_gnss)
    result["timestamp_system_utc_start"] = start_utc
    result["timestamp_monotonic_ns_start"] = start_ns
    try:
        completed = subprocess.run(
            command,
            capture_output=True,
            text=True,
            timeout=config.timeout_s,
            check=False,
        )
        result["timestamp_system_utc_end"] = utc_now_iso()
        result["timestamp_monotonic_ns_end"] = time.monotonic_ns()
        raw = completed.stdout or completed.stderr or ""
        result["raw_json"] = raw
        payload = _load_json(raw)
        if completed.returncode == 0 and payload is not None and "error" not in payload:
            result["success"] = 1
            result["bitrate_bps"] = _bitrate(payload, direction)
            result["retransmits"] = _retransmits(payload)
            result["error_text"] = None
        else:
            result["success"] = 0
            api_error = payload.get("error") if isinstance(payload, dict) else None
            result["error_text"] = api_error or f"iperf3 exited with code {completed.returncode}"
    except Exception as exc:
        result["timestamp_system_utc_end"] = utc_now_iso()
        result["timestamp_monotonic_ns_end"] = time.monotonic_ns()
        result["success"] = 0
        result["error_text"] = str(exc)
    return result


def _base_result(
    config: IperfConfig,
    direction: str,
    bytes_requested: str,
    port: int,
    ref_gnss: GnssState,
) -> dict[str, Any]:
    return {
        "timestamp_system_utc_start": utc_now_iso(),
        "timestamp_system_utc_end": None,
        "timestamp_monotonic_ns_start": time.monotonic_ns(),
        "timestamp_monotonic_ns_end": None,
        "ref_gnss_time_utc": ref_gnss.gnss_time_utc,
        "ref_gnss_latitude": ref_gnss.latitude,
        "ref_gnss_longitude": ref_gnss.longitude,
        "server": config.server,
        "port": port,
        "protocol": config.protocol,
        "direction": direction,
        "mode": config.mode,
        "bytes_requested": bytes_requested,
        "parallel_streams": config.parallel_streams,
        "success": 0,
        "bitrate_bps": None,
        "retransmits": None,
        "raw_json": None,
        "error_text": None,
    }


def _load_json(raw: str) -> dict[str, Any] | None:
    try:
        payload = json.loads(raw)
    except json.JSONDecodeError:
        return None
    return payload if isinstance(payload, dict) else None


def _bitrate(payload: dict[str, Any], direction: str) -> float | None:
    end = payload.get("end", {})
    key = "sum_received" if direction == "download" else "sum_sent"
    section = end.get(key, {})
    bitrate = section.get("bits_per_second")
    return float(bitrate) if bitrate is not None else None


def _retransmits(payload: dict[str, Any]) -> int | None:
    retransmits = payload.get("end", {}).get("sum_sent", {}).get("retransmits")
    return int(retransmits) if retransmits is not None else None
