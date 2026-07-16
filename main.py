from __future__ import annotations

import argparse
import logging
import sys
from pathlib import Path

from measurement.config import load_config


def main() -> int:
    parser = argparse.ArgumentParser(description="Mobile Cellulink/GNSS measurement program")
    parser.add_argument(
        "--config",
        default=str(Path(__file__).with_name("config.ini")),
        help="Path to config.ini",
    )
    parser.add_argument(
        "--no-gpio",
        action="store_true",
        help="Development mode without GPIO button and status LED",
    )
    parser.add_argument(
        "--start-now",
        action="store_true",
        help="Start a measurement immediately (development/debugging)",
    )
    parser.add_argument(
        "--test",
        action="store_true",
        help="Start interactive shell test mode instead of normal measurement service mode",
    )
    parser.add_argument(
        "--test-hardware",
        choices=["none", "gnss", "cellulink", "modem-toggle", "led", "both"],
        help="Hardware to test in --test mode; omit for interactive menu",
    )
    parser.add_argument(
        "--test-button",
        action="store_true",
        help="Also test the GPIO button in --test mode",
    )
    parser.add_argument(
        "--test-led-state",
        choices=["IDLE", "STARTING", "RUNNING", "STOPPING", "ERROR"],
        help="Status LED state to show in --test-hardware led mode",
    )
    parser.add_argument(
        "--test-seconds",
        type=int,
        default=30,
        help="Test duration in seconds; use 0 to run until q + Enter",
    )
    args = parser.parse_args()

    config = load_config(args.config)
    if args.test:
        from measurement.hardware_test import run_hardware_test

        return run_hardware_test(
            config,
            hardware=args.test_hardware,
            use_button=args.test_button,
            led_state=args.test_led_state,
            duration_s=args.test_seconds,
        )

    logger = _configure_logging(config.measurement.database_path.parent / "system.log")

    from measurement.controller import MeasurementController
    from measurement.database import MeasurementDatabase
    from measurement.gpio_control import GpioButtonControl
    from measurement.status_led import StatusLed

    database = MeasurementDatabase(config.measurement.database_path)
    status_led = StatusLed(config.status_led)
    button: GpioButtonControl | None = None
    controller: MeasurementController | None = None
    return_code = 0

    database.log_system_event(None, "PROGRAM_START", "Messprogramm gestartet")
    database.log_system_event(None, "SERVICE_START", "Boot-/Service-Start")
    logger.info("Messprogramm gestartet; Datenbank: %s", config.measurement.database_path)

    try:
        if not args.no_gpio:
            try:
                status_led.start()
            except Exception as exc:
                logger.exception("Status-LED konnte nicht initialisiert werden")
                database.log_error(None, "status_led", str(exc))
                database.log_system_event(None, "GPIO_ERROR", str(exc), {"component": "status_led"})
            button = GpioButtonControl(config.gpio)
            try:
                button.start()
            except Exception as exc:
                logger.exception("GPIO-Taster konnte nicht initialisiert werden")
                database.log_error(None, "gpio_button", str(exc))
                database.log_system_event(None, "GPIO_ERROR", str(exc), {"component": "button"})
                raise
        controller = MeasurementController(config, database, button, status_led, logger)
        controller.run(start_immediately=args.start_now)
    except KeyboardInterrupt:
        logger.info("Ctrl+C im Entwicklungs-/Debugbetrieb erkannt")
    except Exception as exc:
        return_code = 1
        logger.exception("Fataler Programmfehler")
        database.log_error(None, "main", str(exc))
        database.log_system_event(None, "PROGRAM_ERROR", str(exc))
    finally:
        if controller is not None:
            try:
                controller.close()
            except Exception:
                return_code = 1
                logger.exception("Fehler bei der abschließenden Bereinigung")
        if button is not None:
            button.stop()
        status_led.stop()
        database.log_system_event(None, "PROGRAM_STOP", "Messprogramm beendet")
        database.close()
    return return_code


def _configure_logging(log_path: Path) -> logging.Logger:
    log_path.parent.mkdir(parents=True, exist_ok=True)
    logger = logging.getLogger("measurement_system")
    logger.setLevel(logging.INFO)
    logger.handlers.clear()
    formatter = logging.Formatter(
        "%(asctime)s %(levelname)s %(threadName)s %(message)s",
        "%Y-%m-%dT%H:%M:%S%z",
    )
    file_handler = logging.FileHandler(log_path, encoding="utf-8")
    file_handler.setFormatter(formatter)
    stream_handler = logging.StreamHandler(sys.stdout)
    stream_handler.setFormatter(formatter)
    logger.addHandler(file_handler)
    logger.addHandler(stream_handler)
    return logger


if __name__ == "__main__":
    raise SystemExit(main())
