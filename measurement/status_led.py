from __future__ import annotations

import threading
from enum import Enum
from typing import Any

from .config import StatusLedConfig


class SystemState(str, Enum):
    IDLE = "IDLE"
    STARTING = "STARTING"
    RUNNING = "RUNNING"
    STOPPING = "STOPPING"
    ERROR = "ERROR"


class StatusLed:
    def __init__(self, config: StatusLedConfig):
        self.config = config
        self._led: Any = None
        self._state = SystemState.IDLE
        self._lock = threading.Lock()
        self._changed = threading.Event()
        self._stop_event = threading.Event()
        self._stop_pattern_done = threading.Event()
        self._stop_pattern_done.set()
        self._thread = threading.Thread(target=self._run, name="status-led", daemon=True)

    def start(self) -> None:
        if not self.config.enabled:
            return
        try:
            from gpiozero import LED
        except ImportError as exc:
            raise RuntimeError("gpiozero is not installed") from exc
        self._led = LED(self.config.gpio)
        self._thread.start()

    def set_state(self, state: SystemState) -> None:
        with self._lock:
            previous_state = self._state
        if (
            previous_state == SystemState.STOPPING
            and state != SystemState.STOPPING
            and self._thread.is_alive()
        ):
            self._stop_pattern_done.wait(timeout=1.2)
        with self._lock:
            self._state = state
            if state == SystemState.STOPPING:
                self._stop_pattern_done.clear()
        self._changed.set()

    def stop(self) -> None:
        self._stop_event.set()
        self._changed.set()
        if self._thread.is_alive():
            self._thread.join(timeout=2)
        if self._led is not None:
            self._led.off()
            self._led.close()

    def _run(self) -> None:
        last_state: SystemState | None = None
        phase_on = False
        stop_flashes = 0
        while not self._stop_event.is_set():
            with self._lock:
                state = self._state
            if state != last_state:
                last_state = state
                phase_on = False
                stop_flashes = 0

            if state == SystemState.RUNNING:
                self._led.on()
                timeout = 1.0
            elif state == SystemState.STOPPING:
                if stop_flashes < 6:
                    phase_on = not phase_on
                    self._led.value = phase_on
                    stop_flashes += 1
                    timeout = 0.15
                else:
                    self._led.off()
                    self._stop_pattern_done.set()
                    timeout = 1.0
            else:
                phase_on = not phase_on
                self._led.value = phase_on
                timeout = 1.0 if state == SystemState.IDLE else 0.2

            self._changed.wait(timeout)
            self._changed.clear()
