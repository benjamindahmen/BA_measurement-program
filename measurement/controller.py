from __future__ import annotations

import logging
import subprocess
import threading
from collections.abc import Callable

from .cellulink_api import CellulinkApiClient
from .cellulink_auth import CellulinkAuthenticator
from .config import AppConfig
from .database import MeasurementDatabase
from .gnss_reference import ReferenceGnssReader
from .gpio_control import ButtonEvent, ButtonEventType, GpioButtonControl
from .scheduler import MeasurementScheduler
from .status_led import StatusLed, SystemState


class MeasurementController:
    def __init__(
        self,
        config: AppConfig,
        database: MeasurementDatabase,
        button: GpioButtonControl | None,
        status_led: StatusLed,
        logger: logging.Logger,
        shutdown_command: Callable[[], None] | None = None,
    ):
        self.config = config
        self.database = database
        self.button = button
        self.status_led = status_led
        self.logger = logger
        self.state = SystemState.IDLE
        self.run_id: int | None = None
        self.scheduler: MeasurementScheduler | None = None
        self.gnss_reader: ReferenceGnssReader | None = None
        self._exit_event = threading.Event()
        self._shutdown_command = shutdown_command or self._shutdown_host

    def run(self, start_immediately: bool = False) -> None:
        self._set_state(SystemState.IDLE)
        if start_immediately:
            self.start_measurement()

        while not self._exit_event.is_set():
            if self.button is None:
                self._exit_event.wait(0.5)
                continue
            event = self.button.get_event(timeout=0.5)
            if event is not None:
                self.handle_button_event(event)

    def request_exit(self) -> None:
        self._exit_event.set()

    def handle_button_event(self, event: ButtonEvent) -> None:
        self.database.log_system_event(
            self.run_id,
            "BUTTON_DETECTED",
            event.event_type.value,
            {"duration_s": round(event.duration_s, 3), "state": self.state.value},
        )
        self.logger.info(
            "Tasterereignis %s nach %.2f s im Zustand %s",
            event.event_type.value,
            event.duration_s,
            self.state.value,
        )

        if event.event_type == ButtonEventType.SHUTDOWN_HOLD:
            self.shutdown()
        elif event.event_type == ButtonEventType.STOP_HOLD and self.state == SystemState.RUNNING:
            self.stop_measurement()
        elif event.event_type == ButtonEventType.SHORT_PRESS and self.state in {
            SystemState.IDLE,
            SystemState.ERROR,
        }:
            self.start_measurement()

    def start_measurement(self) -> None:
        if self.state not in {SystemState.IDLE, SystemState.ERROR}:
            return
        self._set_state(SystemState.STARTING)
        try:
            self.run_id = self.database.create_run(
                self.config.measurement.route_id,
                self.config.measurement.direction,
                self.config.measurement.vehicle,
                self.config.measurement.notes,
                self.config.redacted_json(),
            )
            self.logger.info("Messfahrt %s wird gestartet", self.run_id)

            authenticator = CellulinkAuthenticator(self.config.cellulink)
            authenticator.reachability_check()
            access_token = authenticator.login()
            api_client = CellulinkApiClient(self.config.cellulink, access_token)

            self.gnss_reader = ReferenceGnssReader(
                self.config.reference_gnss.port,
                self.config.reference_gnss.baudrate,
                self.config.reference_gnss.read_timeout_s,
                on_error=self._on_gnss_error,
            )
            self.gnss_reader.start()
            self._save_startup_snapshots(api_client)

            self.scheduler = MeasurementScheduler(
                self.config,
                self.database,
                self.run_id,
                api_client,
                self.gnss_reader,
            )
            self.scheduler.start()
            self.database.log_system_event(self.run_id, "MEASUREMENT_STARTED", "Messfahrt gestartet")
            self._set_state(SystemState.RUNNING)
        except Exception as exc:
            self.logger.exception("Messfahrt konnte nicht gestartet werden")
            self.database.log_error(self.run_id, "controller_start", str(exc))
            self.database.log_system_event(self.run_id, "API_ERROR", str(exc))
            try:
                self._cleanup_run(mark_finished=True)
            except Exception as cleanup_exc:
                self.logger.exception("Bereinigung nach Startfehler fehlgeschlagen")
                self.database.log_error(self.run_id, "controller_cleanup", str(cleanup_exc))
            self._set_state(SystemState.ERROR)

    def stop_measurement(self) -> None:
        if self.state not in {SystemState.RUNNING, SystemState.STARTING}:
            return
        self._set_state(SystemState.STOPPING)
        active_run_id = self.run_id
        try:
            self._cleanup_run(mark_finished=True)
        except Exception as exc:
            self.logger.exception("Fehler beim Beenden der Messfahrt %s", active_run_id)
            self.database.log_error(active_run_id, "controller_stop", str(exc))
            self.database.log_system_event(active_run_id, "STOP_ERROR", str(exc))
            self._set_state(SystemState.ERROR)
            return
        self.database.log_system_event(active_run_id, "MEASUREMENT_STOPPED", "Messfahrt beendet")
        self.logger.info("Messfahrt %s wurde sauber beendet", active_run_id)
        self._set_state(SystemState.IDLE)

    def shutdown(self) -> None:
        self.database.log_system_event(
            self.run_id,
            "SHUTDOWN_REQUESTED",
            "Herunterfahren per langem Tastendruck angefordert",
        )
        self.logger.info("Herunterfahren wurde angefordert")
        if self.state in {SystemState.RUNNING, SystemState.STARTING}:
            self.stop_measurement()
        try:
            self._shutdown_command()
        except Exception as exc:
            self.logger.exception("Herunterfahren fehlgeschlagen")
            self.database.log_error(None, "shutdown", str(exc))
            self.database.log_system_event(None, "SHUTDOWN_ERROR", str(exc))
            self._set_state(SystemState.ERROR)
            return
        self._exit_event.set()

    def close(self) -> None:
        if self.run_id is not None:
            if self.state in {SystemState.RUNNING, SystemState.STARTING}:
                self.stop_measurement()
            else:
                self._cleanup_run(mark_finished=True)
        self._exit_event.set()

    def _cleanup_run(self, mark_finished: bool) -> None:
        errors: list[Exception] = []
        if self.scheduler is not None:
            try:
                self.scheduler.stop()
            except Exception as exc:
                errors.append(exc)
            self.scheduler = None
        if self.gnss_reader is not None:
            try:
                self.gnss_reader.stop()
                self.gnss_reader.join()
            except Exception as exc:
                errors.append(exc)
            self.gnss_reader = None
        if mark_finished and self.run_id is not None:
            try:
                self.database.finish_run(self.run_id)
            except Exception as exc:
                errors.append(exc)
        self.run_id = None
        if errors:
            raise RuntimeError("; ".join(str(error) for error in errors)) from errors[0]

    def _on_gnss_error(self, message: str, details: dict | None = None) -> None:
        self.database.log_error(self.run_id, "reference_gnss", message, details)
        self.database.log_system_event(self.run_id, "GNSS_ERROR", message, details)
        self.logger.error("GNSS-Fehler: %s", message)

    def _save_startup_snapshots(self, api_client: CellulinkApiClient) -> None:
        if self.run_id is None:
            return
        snapshots = [
            (
                f"/api/v1/cellular/modems/{api_client.config.modem_id}/profiles/"
                f"{api_client.config.profile_id}/status",
                "cellular_profile_status",
                api_client.get_profile_status,
            ),
            (
                f"/api/v1/cellular/modems/{api_client.config.modem_id}/profiles/"
                f"{api_client.config.profile_id}/configuration",
                "modem_profile_configuration",
                api_client.get_profile_configuration,
            ),
            (
                f"/api/v1/cellular/modems/{api_client.config.modem_id}/configuration",
                "cellular_configuration",
                api_client.get_modem_configuration,
            ),
            (
                f"/api/v1/cellular/modems/{api_client.config.modem_id}/information",
                "cellular_information",
                api_client.get_modem_information,
            ),
        ]
        for endpoint, snapshot_type, func in snapshots:
            try:
                payload = func()
                self.database.save_startup_snapshot(
                    self.run_id, endpoint, snapshot_type, True, payload=payload
                )
            except Exception as exc:
                self.database.save_startup_snapshot(
                    self.run_id, endpoint, snapshot_type, False, error_text=str(exc)
                )
                self.database.log_error(
                    self.run_id, f"startup_snapshot:{snapshot_type}", str(exc)
                )
                self.database.log_system_event(
                    self.run_id,
                    "API_ERROR",
                    str(exc),
                    {"snapshot_type": snapshot_type},
                )

    def _set_state(self, state: SystemState) -> None:
        self.state = state
        self.status_led.set_state(state)
        self.logger.info("Systemzustand: %s", state.value)

    @staticmethod
    def _shutdown_host() -> None:
        subprocess.run(["sudo", "shutdown", "-h", "now"], check=True)
