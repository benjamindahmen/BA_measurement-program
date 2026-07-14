from __future__ import annotations

import logging
import subprocess
import threading
import time
from collections.abc import Callable

from .cellulink_api import CellulinkApiClient, extract_cellulink_gnss_fields
from .cellulink_auth import CellulinkAuthenticator
from .config import AppConfig, PingConfig
from .database import MeasurementDatabase
from .gnss_reference import ReferenceGnssReader
from .gpio_control import ButtonEvent, ButtonEventType, GpioButtonControl
from .models import GnssState
from .ping_test import run_ping
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
        self._last_status_action: str | None = None

    def run(self, start_immediately: bool = False) -> None:
        self._set_state(SystemState.IDLE)
        self._report_status("Warte auf kurzen Tastendruck zum Starten")
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
        self._report_status(
            f"Taster erkannt: {event.event_type.value} nach {event.duration_s:.2f} s"
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
            self._report_status("Lege neue Messfahrt in SQLite an")
            self.run_id = self.database.create_run(
                self.config.measurement.route_id,
                self.config.measurement.direction,
                self.config.measurement.vehicle,
                self.config.measurement.notes,
                self.config.redacted_json(),
            )
            self.logger.info("Messfahrt %s wird gestartet", self.run_id)

            self._report_status("Prüfe Cellulink-Erreichbarkeit")
            authenticator = CellulinkAuthenticator(self.config.cellulink)
            authenticator.reachability_check()
            self._report_status("Melde am Cellulink an")
            access_token = authenticator.login()
            api_client = CellulinkApiClient(self.config.cellulink, access_token)

            self._reset_cellular_connection(api_client)

            self._report_status("Starte Referenz-GNSS-Reader")
            self.gnss_reader = ReferenceGnssReader(
                self.config.reference_gnss.port,
                self.config.reference_gnss.baudrate,
                self.config.reference_gnss.read_timeout_s,
                on_error=self._on_gnss_error,
            )
            self.gnss_reader.start()
            self._report_status("Speichere Startup-Snapshots")
            self._save_startup_snapshots(api_client)
            self._wait_until_ready(api_client)

            self._report_status("Starte zyklische Messwerterfassung")
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
            self._report_status("Messung läuft")
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
        self._report_status("Beende Messfahrt sauber")
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
        self._report_status("Warte auf kurzen Tastendruck zum Starten")

    def shutdown(self) -> None:
        self._report_status("Shutdown per Taster angefordert")
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

    def _reset_cellular_connection(self, api_client: CellulinkApiClient) -> None:
        if not self.config.startup.cellular_reset_enabled:
            self._report_status("Mobilfunk-Neuanmeldung ist deaktiviert")
            return
        self._report_status("Melde Mobilfunkprofil per API ab")
        self.logger.info("Mobilfunkprofil wird per API abgemeldet")
        self.database.log_system_event(
            self.run_id,
            "CELLULAR_RESET_STARTED",
            "Mobilfunkprofil wird per API ab- und wieder angemeldet",
        )
        api_client.disconnect_cellular_profile(
            self.config.startup.cellular_disconnect_path,
            self.config.startup.cellular_disconnect_method,
        )
        self._report_status("Warte nach Mobilfunk-Abmeldung")
        time.sleep(max(self.config.startup.cellular_reset_settle_s, 0.0))
        self._report_status("Melde Mobilfunkprofil per API wieder an")
        self.logger.info("Mobilfunkprofil wird per API wieder angemeldet")
        api_client.connect_cellular_profile(
            self.config.startup.cellular_connect_path,
            self.config.startup.cellular_connect_method,
        )
        self.database.log_system_event(
            self.run_id,
            "CELLULAR_RESET_FINISHED",
            "Mobilfunkprofil wurde per API neu angemeldet",
        )

    def _wait_until_ready(self, api_client: CellulinkApiClient) -> None:
        deadline = time.monotonic() + max(self.config.startup.ready_timeout_s, 1.0)
        startup_ping_done = False
        last_status = ""
        next_heartbeat = 0.0

        while time.monotonic() < deadline:
            ref_ready = self._reference_gnss_ready()
            cellulink_ready = self._cellulink_gnss_ready(api_client)
            if ref_ready and cellulink_ready and not startup_ping_done:
                self._report_status(f"Führe Start-Ping zu {self.config.ping.target} aus", force=True)
                startup_ping_done = self._run_startup_ping()
            if ref_ready and cellulink_ready and startup_ping_done:
                self.database.log_system_event(
                    self.run_id,
                    "STARTUP_READY",
                    "Referenz-GNSS, Cellulink-GNSS und Mobilfunk-Ping bereit",
                )
                self.logger.info("Startbedingungen erfüllt")
                self._report_status("Startbedingungen erfüllt")
                return

            if not ref_ready:
                action = "Warte auf Fix von Referenz-GNSS"
            elif not cellulink_ready:
                action = "Warte auf Fix von Cellulink-GNSS"
            else:
                action = f"Warte auf erfolgreichen Start-Ping zu {self.config.ping.target}"

            last_status = (
                f"ref_gnss={ref_ready}, cellulink_gnss={cellulink_ready}, "
                f"ping={startup_ping_done}"
            )
            now = time.monotonic()
            self._report_status(f"{action} ({last_status})", force=now >= next_heartbeat)
            if now >= next_heartbeat:
                next_heartbeat = now + 10.0
            time.sleep(max(self.config.startup.check_interval_s, 0.5))

        raise TimeoutError(f"Startbedingungen nicht rechtzeitig erfüllt: {last_status}")

    def _reference_gnss_ready(self) -> bool:
        if self.gnss_reader is None:
            return False
        return bool(self.gnss_reader.snapshot().valid)

    def _cellulink_gnss_ready(self, api_client: CellulinkApiClient) -> bool:
        try:
            status = api_client.get_gnss_status()
            info = api_client.get_gnss_information()
        except Exception as exc:
            self.database.log_error(self.run_id, "startup_cellulink_gnss", str(exc))
            return False
        fields = extract_cellulink_gnss_fields(status, info)
        has_position = (
            fields.get("cellulink_gnss_latitude") is not None
            and fields.get("cellulink_gnss_longitude") is not None
        )
        used_satellites = fields.get("cellulink_gnss_used_satellites") or 0
        status_text = str(fields.get("cellulink_gnss_status") or "").lower()
        mode_text = str(fields.get("cellulink_gnss_mode") or "").lower()
        status_indicates_fix = any(marker in status_text for marker in ("fix", "valid", "3d", "2d"))
        mode_indicates_fix = any(marker in mode_text for marker in ("fix", "3d", "2d"))
        return bool(has_position and (used_satellites > 0 or status_indicates_fix or mode_indicates_fix))

    def _run_startup_ping(self) -> bool:
        ping_config = PingConfig(
            enabled=True,
            target=self.config.ping.target,
            interval_s=self.config.ping.interval_s,
            count=max(self.config.startup.ping_count, 1),
            timeout_s=max(self.config.startup.ping_timeout_s, 1),
        )
        result = run_ping(ping_config, GnssState())
        result["run_id"] = self.run_id
        self.database.insert_ping_result(result)
        if result["success"]:
            self.database.log_system_event(
                self.run_id,
                "STARTUP_PING_OK",
                f"Start-Ping zu {ping_config.target} erfolgreich",
            )
            return True
        self.database.log_error(self.run_id, "startup_ping", result.get("error_text") or "startup ping failed")
        self.database.log_system_event(
            self.run_id,
            "STARTUP_PING_FAILED",
            result.get("error_text") or "startup ping failed",
        )
        return False

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
        self.logger.info("STATUS state=%s action=Zustand gewechselt", state.value)

    def _report_status(self, action: str, *, force: bool = False) -> None:
        changed = action != self._last_status_action
        if not force and not changed:
            return
        self._last_status_action = action
        self.logger.info("STATUS state=%s action=%s", self.state.value, action)
        if changed:
            self.database.log_system_event(
                self.run_id,
                "STATUS",
                action,
                {"state": self.state.value},
            )

    @staticmethod
    def _shutdown_host() -> None:
        subprocess.run(["sudo", "shutdown", "-h", "now"], check=True)
