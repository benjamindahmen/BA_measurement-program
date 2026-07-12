from __future__ import annotations

import queue
import threading
import time
from dataclasses import dataclass
from enum import Enum
from typing import Any

from .config import GpioConfig


class ButtonEventType(str, Enum):
    SHORT_PRESS = "SHORT_PRESS"
    STOP_HOLD = "STOP_HOLD"
    SHUTDOWN_HOLD = "SHUTDOWN_HOLD"


@dataclass(frozen=True)
class ButtonEvent:
    event_type: ButtonEventType
    duration_s: float


class GpioButtonControl:
    """Turn GPIO edge callbacks into non-blocking events for the controller."""

    def __init__(self, config: GpioConfig):
        self.config = config
        self.events: queue.Queue[ButtonEvent] = queue.Queue()
        self._button: Any = None
        self._lock = threading.Lock()
        self._pressed_at: float | None = None
        self._stop_sent = False
        self._shutdown_sent = False
        self._stop_event = threading.Event()
        self._wake_event = threading.Event()
        self._monitor = threading.Thread(
            target=self._monitor_hold_time,
            name="gpio-button-monitor",
            daemon=True,
        )

    def start(self) -> None:
        if self.config.shutdown_hold_time_s <= self.config.stop_hold_time_s:
            raise ValueError("SHUTDOWN_HOLD_TIME_S must be greater than STOP_HOLD_TIME_S")

        try:
            from gpiozero import Button
        except ImportError as exc:
            raise RuntimeError("gpiozero is not installed") from exc

        self._button = Button(
            self.config.button_gpio,
            pull_up=True,
            bounce_time=max(self.config.button_bounce_time_s, 0.0),
        )
        self._button.when_pressed = self._on_pressed
        self._button.when_released = self._on_released
        self._monitor.start()

    def stop(self) -> None:
        self._stop_event.set()
        self._wake_event.set()
        if self._monitor.is_alive():
            self._monitor.join(timeout=2)
        if self._button is not None:
            self._button.close()

    def get_event(self, timeout: float = 0.5) -> ButtonEvent | None:
        try:
            return self.events.get(timeout=timeout)
        except queue.Empty:
            return None

    def _on_pressed(self) -> None:
        with self._lock:
            self._pressed_at = time.monotonic()
            self._stop_sent = False
            self._shutdown_sent = False
        self._wake_event.set()

    def _on_released(self) -> None:
        pending: list[ButtonEvent] = []
        with self._lock:
            if self._pressed_at is None:
                return
            duration = time.monotonic() - self._pressed_at
            if duration >= self.config.shutdown_hold_time_s and not self._shutdown_sent:
                pending.append(ButtonEvent(ButtonEventType.SHUTDOWN_HOLD, duration))
            elif duration >= self.config.stop_hold_time_s and not self._stop_sent:
                pending.append(ButtonEvent(ButtonEventType.STOP_HOLD, duration))
            elif duration < self.config.stop_hold_time_s:
                pending.append(ButtonEvent(ButtonEventType.SHORT_PRESS, duration))
            self._pressed_at = None
        for event in pending:
            self.events.put(event)
        self._wake_event.set()

    def _monitor_hold_time(self) -> None:
        while not self._stop_event.is_set():
            pending: list[ButtonEvent] = []
            with self._lock:
                if self._pressed_at is not None:
                    duration = time.monotonic() - self._pressed_at
                    if duration >= self.config.stop_hold_time_s and not self._stop_sent:
                        self._stop_sent = True
                        pending.append(ButtonEvent(ButtonEventType.STOP_HOLD, duration))
                    if duration >= self.config.shutdown_hold_time_s and not self._shutdown_sent:
                        self._shutdown_sent = True
                        pending.append(ButtonEvent(ButtonEventType.SHUTDOWN_HOLD, duration))
            for event in pending:
                self.events.put(event)
            self._wake_event.wait(0.05)
            self._wake_event.clear()
