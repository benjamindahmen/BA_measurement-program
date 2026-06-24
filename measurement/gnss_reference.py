from __future__ import annotations

import copy
import threading
from datetime import datetime, timezone
from typing import Callable

import pynmea2
import serial

from .models import GnssState, to_float, to_int


class ReferenceGnssReader(threading.Thread):
    def __init__(
        self,
        port: str,
        baudrate: int,
        read_timeout_s: float,
        on_error: Callable[[str, dict | None], None] | None = None,
    ):
        super().__init__(name="reference-gnss", daemon=True)
        self.port = port
        self.baudrate = baudrate
        self.read_timeout_s = read_timeout_s
        self.on_error = on_error
        self._stop_event = threading.Event()
        self._lock = threading.Lock()
        self._state = GnssState()
        self._last_rmc_status: str | None = None

    def run(self) -> None:
        try:
            with serial.Serial(self.port, self.baudrate, timeout=self.read_timeout_s) as ser:
                while not self._stop_event.is_set():
                    raw = ser.readline()
                    if not raw:
                        continue
                    line = raw.decode("ascii", errors="ignore").strip()
                    if not line:
                        continue
                    self._handle_line(line)
        except Exception as exc:  # The scheduler keeps running and records empty GNSS values.
            if self.on_error:
                self.on_error(str(exc), {"port": self.port, "baudrate": self.baudrate})

    def stop(self) -> None:
        self._stop_event.set()

    def snapshot(self) -> GnssState:
        with self._lock:
            return copy.deepcopy(self._state)

    def _handle_line(self, line: str) -> None:
        try:
            msg = pynmea2.parse(line)
        except pynmea2.ParseError:
            return

        with self._lock:
            if isinstance(msg, pynmea2.types.talker.RMC):
                self._last_rmc_status = getattr(msg, "status", None)
                self._state.raw_rmc = line
                self._state.gnss_time_utc = _rmc_datetime_iso(msg)
                self._state.latitude = to_float(getattr(msg, "latitude", None))
                self._state.longitude = to_float(getattr(msg, "longitude", None))
                self._state.speed_kmh = _knots_to_kmh(to_float(getattr(msg, "spd_over_grnd", None)))
                self._state.course_deg = to_float(getattr(msg, "true_course", None))
                self._state.valid = _valid(self._state, self._last_rmc_status)
            elif isinstance(msg, pynmea2.types.talker.GGA):
                self._state.raw_gga = line
                self._state.altitude_m = to_float(getattr(msg, "altitude", None))
                self._state.fix_quality = to_int(getattr(msg, "gps_qual", None))
                self._state.satellites_used = to_int(getattr(msg, "num_sats", None))
                self._state.hdop = to_float(getattr(msg, "horizontal_dil", None))
                self._state.valid = _valid(self._state, self._last_rmc_status)


def _rmc_datetime_iso(msg: pynmea2.types.talker.RMC) -> str | None:
    datestamp = getattr(msg, "datestamp", None)
    timestamp = getattr(msg, "timestamp", None)
    if datestamp is None or timestamp is None:
        return None
    dt = datetime.combine(datestamp, timestamp, tzinfo=timezone.utc)
    return dt.isoformat(timespec="milliseconds").replace("+00:00", "Z")


def _knots_to_kmh(value: float | None) -> float | None:
    if value is None:
        return None
    return value * 1.852


def _valid(state: GnssState, rmc_status: str | None) -> bool:
    rmc_ok = rmc_status == "A"
    gga_ok = (state.fix_quality or 0) > 0
    return bool(rmc_ok and gga_ok)
