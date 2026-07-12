from __future__ import annotations

import sqlite3
import threading
import time
from pathlib import Path
from typing import Any

from .models import json_dumps, utc_now_iso


class MeasurementDatabase:
    def __init__(self, path: Path):
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._conn = sqlite3.connect(self.path, check_same_thread=False)
        self._conn.row_factory = sqlite3.Row
        self._lock = threading.Lock()
        self.initialize()

    def initialize(self) -> None:
        with self._lock, self._conn:
            self._conn.executescript(
                """
                PRAGMA journal_mode=WAL;
                PRAGMA foreign_keys=ON;

                CREATE TABLE IF NOT EXISTS measurement_runs (
                    run_id INTEGER PRIMARY KEY AUTOINCREMENT,
                    start_time_system_utc TEXT NOT NULL,
                    end_time_system_utc TEXT,
                    route_id TEXT,
                    direction TEXT,
                    vehicle TEXT,
                    notes TEXT,
                    config_json TEXT
                );

                CREATE TABLE IF NOT EXISTS startup_snapshots (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    run_id INTEGER NOT NULL,
                    timestamp_system_utc TEXT NOT NULL,
                    endpoint TEXT NOT NULL,
                    snapshot_type TEXT NOT NULL,
                    success INTEGER NOT NULL,
                    payload_json TEXT,
                    error_text TEXT
                );

                CREATE TABLE IF NOT EXISTS samples_1hz (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    run_id INTEGER NOT NULL,
                    sample_index INTEGER NOT NULL,
                    timestamp_system_utc TEXT NOT NULL,
                    timestamp_monotonic_ns INTEGER NOT NULL,
                    ref_gnss_time_utc TEXT,
                    ref_gnss_valid INTEGER,
                    ref_gnss_latitude REAL,
                    ref_gnss_longitude REAL,
                    ref_gnss_speed_kmh REAL,
                    ref_gnss_course_deg REAL,
                    ref_gnss_altitude_m REAL,
                    ref_gnss_fix_quality INTEGER,
                    ref_gnss_satellites_used INTEGER,
                    ref_gnss_hdop REAL,
                    ref_gnss_raw_rmc TEXT,
                    ref_gnss_raw_gga TEXT,
                    cellular_active_sim_profile TEXT,
                    cellular_cell_id TEXT,
                    cellular_technology TEXT,
                    cellular_frequency_band TEXT,
                    cellular_lac TEXT,
                    cellular_mcc TEXT,
                    cellular_mnc TEXT,
                    cellular_packet_data_online TEXT,
                    cellular_registration_status TEXT,
                    cellular_signal_rating TEXT,
                    cellular_rsrp REAL,
                    cellular_rsrq REAL,
                    cellular_rssi REAL,
                    cellular_sinr REAL,
                    cellular_status_json TEXT,
                    cellulink_gnss_status TEXT,
                    cellulink_gnss_time TEXT,
                    cellulink_gnss_date TEXT,
                    cellulink_gnss_latitude REAL,
                    cellulink_gnss_longitude REAL,
                    cellulink_gnss_speed_kmh REAL,
                    cellulink_gnss_speed_mps REAL,
                    cellulink_gnss_altitude_m REAL,
                    cellulink_gnss_mode TEXT,
                    cellulink_gnss_used_satellites INTEGER,
                    cellulink_gnss_visible_satellites INTEGER,
                    cellulink_gnss_track_angle REAL,
                    cellulink_gnss_status_json TEXT,
                    cellulink_gnss_json TEXT,
                    error_json TEXT
                );

                CREATE TABLE IF NOT EXISTS ping_results (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    run_id INTEGER NOT NULL,
                    timestamp_system_utc TEXT NOT NULL,
                    timestamp_monotonic_ns INTEGER NOT NULL,
                    ref_gnss_time_utc TEXT,
                    ref_gnss_latitude REAL,
                    ref_gnss_longitude REAL,
                    target TEXT,
                    count INTEGER,
                    success INTEGER,
                    transmitted INTEGER,
                    received INTEGER,
                    packet_loss_percent REAL,
                    rtt_min_ms REAL,
                    rtt_avg_ms REAL,
                    rtt_max_ms REAL,
                    raw_output TEXT,
                    error_text TEXT
                );

                CREATE TABLE IF NOT EXISTS iperf_results (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    run_id INTEGER NOT NULL,
                    timestamp_system_utc_start TEXT NOT NULL,
                    timestamp_system_utc_end TEXT,
                    timestamp_monotonic_ns_start INTEGER,
                    timestamp_monotonic_ns_end INTEGER,
                    ref_gnss_time_utc TEXT,
                    ref_gnss_latitude REAL,
                    ref_gnss_longitude REAL,
                    server TEXT,
                    port INTEGER,
                    protocol TEXT,
                    direction TEXT,
                    mode TEXT,
                    bytes_requested TEXT,
                    parallel_streams INTEGER,
                    success INTEGER,
                    bitrate_bps REAL,
                    retransmits INTEGER,
                    raw_json TEXT,
                    error_text TEXT
                );

                CREATE TABLE IF NOT EXISTS error_log (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    run_id INTEGER,
                    timestamp_system_utc TEXT NOT NULL,
                    source TEXT NOT NULL,
                    message TEXT NOT NULL,
                    details_json TEXT
                );

                CREATE TABLE IF NOT EXISTS system_events (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    run_id INTEGER,
                    timestamp_system_utc TEXT NOT NULL,
                    timestamp_monotonic_ns INTEGER NOT NULL,
                    event_type TEXT NOT NULL,
                    message TEXT,
                    details_json TEXT
                );
                """
            )

    def create_run(self, route_id: str, direction: str, vehicle: str, notes: str, config_json: str) -> int:
        with self._lock, self._conn:
            cursor = self._conn.execute(
                """
                INSERT INTO measurement_runs
                (start_time_system_utc, route_id, direction, vehicle, notes, config_json)
                VALUES (?, ?, ?, ?, ?, ?)
                """,
                (utc_now_iso(), route_id, direction, vehicle, notes, config_json),
            )
            return int(cursor.lastrowid)

    def finish_run(self, run_id: int) -> None:
        with self._lock, self._conn:
            self._conn.execute(
                "UPDATE measurement_runs SET end_time_system_utc = ? WHERE run_id = ?",
                (utc_now_iso(), run_id),
            )

    def save_startup_snapshot(
        self,
        run_id: int,
        endpoint: str,
        snapshot_type: str,
        success: bool,
        payload: Any = None,
        error_text: str | None = None,
    ) -> None:
        with self._lock, self._conn:
            self._conn.execute(
                """
                INSERT INTO startup_snapshots
                (run_id, timestamp_system_utc, endpoint, snapshot_type, success, payload_json, error_text)
                VALUES (?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    run_id,
                    utc_now_iso(),
                    endpoint,
                    snapshot_type,
                    1 if success else 0,
                    json_dumps(payload) if payload is not None else None,
                    error_text,
                ),
            )

    def insert_sample(self, data: dict[str, Any]) -> None:
        self._insert_dict("samples_1hz", data)

    def insert_ping_result(self, data: dict[str, Any]) -> None:
        self._insert_dict("ping_results", data)

    def insert_iperf_result(self, data: dict[str, Any]) -> None:
        self._insert_dict("iperf_results", data)

    def log_error(self, run_id: int | None, source: str, message: str, details: Any = None) -> None:
        with self._lock, self._conn:
            self._conn.execute(
                """
                INSERT INTO error_log
                (run_id, timestamp_system_utc, source, message, details_json)
                VALUES (?, ?, ?, ?, ?)
                """,
                (run_id, utc_now_iso(), source, message, json_dumps(details) if details is not None else None),
            )

    def log_system_event(
        self,
        run_id: int | None,
        event_type: str,
        message: str | None = None,
        details: Any = None,
    ) -> None:
        with self._lock, self._conn:
            self._conn.execute(
                """
                INSERT INTO system_events
                (run_id, timestamp_system_utc, timestamp_monotonic_ns, event_type, message, details_json)
                VALUES (?, ?, ?, ?, ?, ?)
                """,
                (
                    run_id,
                    utc_now_iso(),
                    time.monotonic_ns(),
                    event_type,
                    message,
                    json_dumps(details) if details is not None else None,
                ),
            )

    def close(self) -> None:
        with self._lock:
            self._conn.close()

    def _insert_dict(self, table: str, data: dict[str, Any]) -> None:
        columns = list(data.keys())
        placeholders = ", ".join(["?"] * len(columns))
        sql = f"INSERT INTO {table} ({', '.join(columns)}) VALUES ({placeholders})"
        values = [data[column] for column in columns]
        with self._lock, self._conn:
            self._conn.execute(sql, values)
