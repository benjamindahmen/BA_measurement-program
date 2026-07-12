from __future__ import annotations

import threading
import time
from typing import Any

from .cellulink_api import (
    CellulinkApiClient,
    extract_cellular_fields,
    extract_cellulink_gnss_fields,
)
from .config import AppConfig
from .database import MeasurementDatabase
from .gnss_reference import ReferenceGnssReader
from .iperf_test import run_iperf
from .models import json_dumps, utc_now_iso
from .ping_test import run_ping


class MeasurementScheduler:
    def __init__(
        self,
        config: AppConfig,
        database: MeasurementDatabase,
        run_id: int,
        api_client: CellulinkApiClient,
        gnss_reader: ReferenceGnssReader,
    ):
        self.config = config
        self.database = database
        self.run_id = run_id
        self.api_client = api_client
        self.gnss_reader = gnss_reader
        self._stop_event = threading.Event()
        self._sample_thread = threading.Thread(target=self._sample_loop, name="sample-1hz", daemon=True)
        self._network_thread = threading.Thread(target=self._network_loop, name="network-tests", daemon=True)

    def start(self) -> None:
        self._sample_thread.start()
        if self.config.ping.enabled or self.config.iperf.enabled:
            self._network_thread.start()

    def stop(self) -> None:
        self._stop_event.set()
        self._sample_thread.join()
        if self._network_thread.is_alive():
            self._network_thread.join()

    def wait_forever(self) -> None:
        while not self._stop_event.is_set():
            time.sleep(0.5)

    def _sample_loop(self) -> None:
        interval = max(self.config.measurement.sample_interval_s, 0.1)
        next_tick = time.monotonic()
        sample_index = 0
        while not self._stop_event.is_set():
            started = time.monotonic()
            sample_index += 1
            self._collect_sample(sample_index)
            next_tick += interval
            sleep_s = max(0.0, next_tick - time.monotonic())
            if sleep_s == 0.0 and time.monotonic() - started > interval:
                next_tick = time.monotonic()
            self._stop_event.wait(sleep_s)

    def _collect_sample(self, sample_index: int) -> None:
        errors: list[dict[str, Any]] = []
        cellular_status: dict[str, Any] | None = None
        gnss_status: dict[str, Any] | None = None
        gnss_info: dict[str, Any] | None = None

        cellular_status = self._api_call("cellular_status", self.api_client.get_cellular_status, errors)
        gnss_status = self._api_call("cellulink_gnss_status", self.api_client.get_gnss_status, errors)
        gnss_info = self._api_call("cellulink_gnss_information", self.api_client.get_gnss_information, errors)

        ref = self.gnss_reader.snapshot()
        data: dict[str, Any] = {
            "run_id": self.run_id,
            "sample_index": sample_index,
            "timestamp_system_utc": utc_now_iso(),
            "timestamp_monotonic_ns": time.monotonic_ns(),
            "ref_gnss_time_utc": ref.gnss_time_utc,
            "ref_gnss_valid": 1 if ref.valid else 0,
            "ref_gnss_latitude": ref.latitude,
            "ref_gnss_longitude": ref.longitude,
            "ref_gnss_speed_kmh": ref.speed_kmh,
            "ref_gnss_course_deg": ref.course_deg,
            "ref_gnss_altitude_m": ref.altitude_m,
            "ref_gnss_fix_quality": ref.fix_quality,
            "ref_gnss_satellites_used": ref.satellites_used,
            "ref_gnss_hdop": ref.hdop,
            "ref_gnss_raw_rmc": ref.raw_rmc,
            "ref_gnss_raw_gga": ref.raw_gga,
        }
        data.update(extract_cellular_fields(cellular_status or {}))
        data.update(extract_cellulink_gnss_fields(gnss_status, gnss_info))
        data["error_json"] = json_dumps(errors) if errors else None
        self.database.insert_sample(data)

    def _api_call(self, source: str, func, errors: list[dict[str, Any]]) -> dict[str, Any] | None:
        try:
            return func()
        except Exception as exc:
            error = {"source": source, "message": str(exc)}
            errors.append(error)
            self.database.log_error(self.run_id, source, str(exc))
            self.database.log_system_event(
                self.run_id, "API_ERROR", str(exc), {"source": source}
            )
            return None

    def _network_loop(self) -> None:
        next_ping = time.monotonic()
        next_iperf = time.monotonic()
        while not self._stop_event.is_set():
            now = time.monotonic()
            if self.config.ping.enabled and now >= next_ping:
                self._run_ping_once()
                next_ping = time.monotonic() + max(self.config.ping.interval_s, 1.0)
            if self.config.iperf.enabled and now >= next_iperf:
                self._run_iperf_pair()
                next_iperf = time.monotonic() + max(self.config.iperf.interval_s, 1.0)
            self._stop_event.wait(0.2)

    def _run_ping_once(self) -> None:
        ref = self.gnss_reader.snapshot()
        result = run_ping(self.config.ping, ref, self._stop_event)
        if self._stop_event.is_set() and result.get("error_text") == "cancelled":
            return
        result["run_id"] = self.run_id
        self.database.insert_ping_result(result)
        if not result["success"]:
            self.database.log_error(self.run_id, "ping", result.get("error_text") or "ping failed")
            self.database.log_system_event(
                self.run_id, "PING_ERROR", result.get("error_text") or "ping failed"
            )

    def _run_iperf_pair(self) -> None:
        for direction in ("upload", "download"):
            if self._stop_event.is_set():
                return
            ref = self.gnss_reader.snapshot()
            result = run_iperf(self.config.iperf, direction, ref, self._stop_event)
            if self._stop_event.is_set() and result.get("error_text") == "cancelled":
                return
            result["run_id"] = self.run_id
            self.database.insert_iperf_result(result)
            if not result["success"]:
                self.database.log_error(
                    self.run_id,
                    f"iperf_{direction}",
                    result.get("error_text") or f"iperf {direction} failed",
                    {"server": result.get("server"), "port": result.get("port")},
                )
                self.database.log_system_event(
                    self.run_id,
                    "IPERF_ERROR",
                    result.get("error_text") or f"iperf {direction} failed",
                    {
                        "direction": direction,
                        "server": result.get("server"),
                        "port": result.get("port"),
                    },
                )
